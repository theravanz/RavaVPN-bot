import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database import (
    PLANS, Payment, SessionMaker, Settings, Subscription, User,
    _utcnow, fmt_money, get_settings, get_stats, upsert_user,
)
from filters import IsAdmin
from keyboards import (
    admin_main_kb, confirm_kb, custom_menu_kb, main_menu_keyboard,
    plan_kb, user_card_kb, users_menu_kb,
)

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class CustomStates(StatesGroup):
    name = State()
    tagline = State()
    locations = State()
    photo = State()


class BroadcastStates(StatesGroup):
    waiting = State()
    confirm = State()


class UserStates(StatesGroup):
    search = State()
    grant = State()


# ========= /admin =========
@router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    s = await get_settings()
    await message.answer(
        f"🛠 <b>Админ-панель</b> | <b>{s.bot_name}</b>\n\nВыберите раздел:",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "adm:main")
async def admin_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_main_kb())


@router.callback_query(F.data == "adm:exit")
async def admin_exit(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("👾 Главное меню", reply_markup=main_menu_keyboard())


# ========= СТАТИСТИКА =========
@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery):
    st = await get_stats()
    s = await get_settings()
    await callback.answer()

    plans_lines = "\n".join(
        f"⏰ {PLANS[key][0]}: {st['plan_counts'].get(key, 0)}"
        for key in ("1d", "3d", "1m", "3m", "6m")
    )

    await callback.message.answer(
        f"📊 <b>Статистика бота {s.bot_name}</b>\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"Всего: <b>{st['total']}</b>\n"
        f"Новых за день: <b>{st['new_day']}</b> | "
        f"за неделю: <b>{st['new_week']}</b> | "
        f"за месяц: <b>{st['new_month']}</b>\n\n"
        f"🔑 <b>Активных подписок: {st['subs_total']}</b>\n"
        f"💎 Платных (3+ дня): <b>{st['subs_paid']}</b>\n"
        f"📅 По тарифам:\n{plans_lines}\n\n"
        f"💰 <b>Пополнения и покупки</b>\n"
        f"Покупок подписок: <b>{st['buys_count']}</b> на <b>{fmt_money(st['buys_sum'])} ₽</b>\n"
        f"Пополнений баланса: <b>{st['topups_count']}</b> на <b>{fmt_money(st['topups_sum'])} ₽</b>",
        reply_markup=admin_main_kb(),
    )


# ========= ПОЛЬЗОВАТЕЛИ И ПОДПИСКИ =========
@router.callback_query(F.data == "adm:users")
async def users_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer("👥 <b>Пользователи и подписки</b>", reply_markup=users_menu_kb())


@router.callback_query(F.data == "usr:menu")
async def users_menu_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer("👥 <b>Пользователи и подписки</b>", reply_markup=users_menu_kb())


@router.callback_query(F.data == "usr:list")
async def users_list(callback: CallbackQuery):
    async with SessionMaker() as session:
        users = (await session.scalars(
            select(User).order_by(User.created_at.desc()).limit(10)
        )).all()

    await callback.answer()
    if not users:
        await callback.message.answer("Пока пусто.", reply_markup=users_menu_kb())
        return

    text = "\n".join(
        f"👤 <code>{u.id}</code> — @{u.username or '—'} — {u.created_at:%d.%m.%y}"
        for u in users
    )
    kb = InlineKeyboardBuilder()
    for u in users:
        kb.button(text=f"👤 @{u.username or u.id}", callback_data=f"usr:card:{u.id}")
    kb.button(text="⬅️ Назад", callback_data="usr:menu")
    kb.adjust(1)
    await callback.message.answer(f"📋 <b>Последние юзеры:</b>\n\n{text}", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("usr:card:"))
async def users_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = int(callback.data.split(":")[2])
    async with SessionMaker() as session:
        u = await session.get(User, uid)
    if not u:
        await callback.answer("❌ Не найден", show_alert=True)
        return
    await callback.answer()
    await _send_user_card(callback.message, u)


@router.callback_query(F.data == "usr:search")
async def users_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.search)
    await callback.answer()
    await callback.message.answer(
        "🔍 Отправь <b>ID</b> юзера или его <b>@username</b>.\n\n<i>Отменить: /cancel</i>"
    )


@router.message(UserStates.search)
async def users_search_do(message: Message, state: FSMContext):
    query = message.text.strip().lstrip("@")
    async with SessionMaker() as session:
        u = await session.get(User, int(query)) if query.isdigit() else None
        if not u:
            u = await session.scalar(select(User).where(User.username == query))
    await state.clear()
    if not u:
        await message.answer("❌ Не найден.", reply_markup=users_menu_kb())
        return
    await _send_user_card(message, u)


async def _send_user_card(message: Message, u: User):
    now = _utcnow()
    async with SessionMaker() as session:
        sub = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == u.id, Subscription.expires_at > now)
            .order_by(Subscription.expires_at.desc())
        )
        paid = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.user_id == u.id)
        ) or 0

    sub_line = f"{PLANS[sub.plan][0]}, до {sub.expires_at:%d.%m.%Y}" if sub else "нет"
    await message.answer(
        f"👤 <code>{u.id}</code> — @{u.username or '—'}\n"
        f"📅 Регистрация: <b>{u.created_at:%d.%m.%Y}</b>\n"
        f"💎 Подписка: <b>{sub_line}</b>\n"
        f"💰 Платежей на: <b>{fmt_money(float(paid))} ₽</b>\n"
        f"⚖️ Баланс: <b>{fmt_money(float(u.balance))} ₽</b>\n"
        f"{'🚫 ЗАБАНЕН' if u.is_banned else '✅ Активен'}",
        reply_markup=user_card_kb(u.id, u.is_banned),
    )


# ========= ВЫДАЧА ПОДПИСКИ ВРУЧНУЮ =========
@router.callback_query(F.data.startswith("usr:grant:"))
async def grant_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":")[2])
    await state.set_state(UserStates.grant)
    await state.update_data(uid=uid)
    await callback.answer()
    await callback.message.answer(
        f"🎁 Выдаём подписку юзеру <code>{uid}</code>.\nВыбери тариф:",
        reply_markup=plan_kb(),
    )


@router.callback_query(UserStates.grant, F.data.startswith("usr:plan:"))
async def grant_apply(callback: CallbackQuery, state: FSMContext):
    plan_key = callback.data.split(":")[2]
    if plan_key not in PLANS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    data = await state.get_data()
    uid = data["uid"]
    _, delta = PLANS[plan_key]
    now = _utcnow()

    async with SessionMaker() as session:
        # продление — от конца текущей активной подписки, а не от сейчас
        current = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == uid, Subscription.expires_at > now)
            .order_by(Subscription.expires_at.desc())
        )
        base = current.expires_at if current else now
        session.add(Subscription(user_id=uid, plan=plan_key, created_at=now, expires_at=base + delta))
        await session.commit()

    await state.clear()
    await callback.answer("✅ Выдано", show_alert=True)
    await callback.message.answer(
        f"✅ <b>{PLANS[plan_key][0]}</b> выдана юзеру <code>{uid}</code>\n"
        f"Действует до: <b>{(base + delta):%d.%m.%Y}</b>"
    )


@router.callback_query(F.data.startswith("usr:ban:"))
async def ban_toggle(callback: CallbackQuery):
    uid = int(callback.data.split(":")[2])
    async with SessionMaker() as session:
        u = await session.get(User, uid)
        if not u:
            await callback.answer("❌ Не найден", show_alert=True)
            return
        u.is_banned = not u.is_banned
        await session.commit()
        banned = u.is_banned
    await callback.answer("🚫 Забанен" if banned else "✅ Разбанен", show_alert=True)
    await _send_user_card(callback.message, u)


# ========= КАСТОМИЗАЦИЯ =========
@router.callback_query(F.data == "adm:custom")
async def admin_custom(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    photo_status = "✅ установлено" if s.main_menu_photo_id else "❌ не установлено"
    await callback.answer()
    await callback.message.answer(
        f"🎨 <b>Кастомизация</b>\n\n"
        f"✏️ Название: <b>{s.bot_name}</b>\n"
        f"💬 Тэглайн: <b>{s.bot_tagline}</b>\n"
        f"🌎 Локаций: <b>{len(s.locations.splitlines())}</b>\n"
        f"🖼 Фото меню: {photo_status}",
        reply_markup=custom_menu_kb(),
    )


async def _prompt(callback: CallbackQuery, state: FSMContext, new_state, text: str):
    await state.set_state(new_state)
    await callback.answer()
    await callback.message.answer(f"{text}\n\n<i>Отменить: /cancel</i>")


@router.callback_query(F.data == "cus:name")
async def cus_name(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.name, "✏️ Отправь новое название бота:")


@router.callback_query(F.data == "cus:tagline")
async def cus_tagline(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.tagline, "💬 Отправь новый тэглайн:")


@router.callback_query(F.data == "cus:locations")
async def cus_locations(callback: CallbackQuery, state: FSMContext):
    await _prompt(
        callback, state, CustomStates.locations,
        "🌎 Отправь локации — каждая с новой строки:\n\n"
        "<code>⚡ Быстрый | АВТО\n🌍 Остальные | АВТО\n📱 Мобильный | АВТО</code>",
    )


@router.callback_query(F.data == "cus:photo")
async def cus_photo(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.photo, "🖼 Отправь картинку для главного меню:")


@router.callback_query(F.data == "cus:photo_del")
async def cus_photo_del(callback: CallbackQuery):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.main_menu_photo_id = None
        await session.commit()
    await callback.answer("🗑 Фото убрано", show_alert=True)


@router.message(CustomStates.name, F.text)
async def save_name(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.bot_name = message.text.strip()[:64]
        await session.commit()
        saved = s.bot_name
    await state.clear()
    await message.answer(f"✅ Название сохранено: <b>{saved}</b>")


@router.message(CustomStates.tagline, F.text)
async def save_tagline(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.bot_tagline = message.text.strip()[:256]
        await session.commit()
        saved = s.bot_tagline
    await state.clear()
    await message.answer(f"✅ Тэглайн сохранён: <i>{saved}</i>")


@router.message(CustomStates.locations, F.text)
async def save_locations(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.locations = message.text.strip()
        await session.commit()
        count = len(s.locations.splitlines())
    await state.clear()
    await message.answer(f"✅ Локации сохранены ({count} шт.)")


@router.message(CustomStates.photo, F.photo)
async def save_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.main_menu_photo_id = file_id
        await session.commit()
    await state.clear()
    await message.answer("✅ Фото сохранено, проверь /start")


# ========= РАССЫЛКА (банненые исключаются) =========
@router.callback_query(F.data == "adm:broadcast")
async def bc_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting)
    await callback.answer()
    await callback.message.answer(
        "📢 Отправь сообщение для рассылки — текст, фото, видео, что угодно.\n\n<i>Отменить: /cancel</i>"
    )


@router.message(BroadcastStates.waiting)
async def bc_preview(message: Message, state: FSMContext):
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(BroadcastStates.confirm)
    await message.answer(
        f"👀 <b>Превью выше</b> ☝️\n\nПолучателей: <b>{await _users_count()}</b>\nОтправляем?",
        reply_markup=confirm_kb(),
    )


@router.message(BroadcastStates.confirm)
async def bc_extra(message: Message):
    await message.answer("⚠️ Сначала подтверди или отмени рассылку кнопками выше.")


@router.callback_query(F.data == "bc:cancel")
async def bc_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("❌ Отменено")
    await callback.message.answer("Рассылка отменена.", reply_markup=admin_main_kb())


@router.callback_query(BroadcastStates.confirm, F.data == "bc:send")
async def bc_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await callback.answer()

    status = await callback.message.edit_text("⏳ Рассылка идёт...")
    ok = failed = 0

    async with SessionMaker() as session:
        user_ids = (await session.scalars(
            select(User.id).where(User.is_banned == False)  # noqa: E712
        )).all()

    for uid in user_ids:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=data["chat_id"],
                message_id=data["message_id"],
            )
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"📢 <b>Рассылка завершена</b>\n\n✅ Доставлено: <b>{ok}</b>\n❌ Не доставлено: <b>{failed}</b>",
        reply_markup=admin_main_kb(),
    )


async def _users_count() -> int:
    async with SessionMaker() as session:
        return await session.scalar(
            select(func.count()).select_from(User).where(User.is_banned == False)  # noqa: E712
        ) or 0


# ========= /cancel =========
@router.message(F.text == "/cancel")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Отменено.", reply_markup=admin_main_kb())