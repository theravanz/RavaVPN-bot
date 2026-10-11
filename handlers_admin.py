import asyncio

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database import (
    Payment, Plan, SessionMaker, Settings, Subscription, User,
    _utcnow, fmt_money, get_admin_summary, get_plan, get_plans,
    get_settings, get_stats,
)
from filters import IsAdmin
from keyboards import (
    admin_main_kb, back_kb, confirm_kb, custom_menu_kb, main_menu_keyboard,
    plan_kb, prices_menu_kb, user_card_kb, users_menu_kb,
)

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class CustomStates(StatesGroup):
    name = State()
    locations = State()
    photo = State()
    price = State()
    channel = State()
    support = State()
    logs = State()
    tz = State()
    trial_days = State()


class BroadcastStates(StatesGroup):
    waiting = State()
    confirm = State()


class UserStates(StatesGroup):
    search = State()
    grant = State()


ADMIN_PANEL_URL = "https://dashboard.render.com"


# ========= ШАПКА ПАНЕЛИ =========
async def _panel_text() -> str:
    s = await get_settings()
    st = await get_admin_summary()
    return (
        f"🛠 <b>Панель управления франшизой {s.bot_name}</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"👥 Пользователей: <b>{st['total']}</b> (+{st['new_today']})\n"
        f"💎 Активных подписок: <b>{st['subs_active']}</b>\n"
        f"💰 Оборот бота: <b>{fmt_money(st['turnover'])} ₽</b>\n"
        f"🛒 Продаж сегодня: <b>{fmt_money(st['sales_today'])} ₽</b>\n\n"
        f"💰 <b>ВАШ БАЛАНС:</b> <b>{fmt_money(float(s.balance))} ₽</b>\n"
        f'🔗 <a href="{ADMIN_PANEL_URL}">Доступ в веб-панель</a>'
    )


async def _render(
    target: Message,
    text: str,
    kb,
    *,
    also_delete: Message | None = None,
):
    try:
        await target.edit_text(text, reply_markup=kb)
    except Exception:
        await target.answer(text, reply_markup=kb)
    if also_delete:
        try:
            await also_delete.delete()
        except Exception:
            pass


@router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await _panel_text(), reply_markup=admin_main_kb())


@router.callback_query(F.data == "adm:main")
async def admin_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render(callback.message, await _panel_text(), admin_main_kb())


@router.callback_query(F.data == "adm:exit")
async def admin_exit(callback: CallbackQuery):
    await callback.answer()
    s = await get_settings()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("👾 Главное меню", reply_markup=main_menu_keyboard(s))


@router.callback_query(F.data.in_({"adm:withdraw", "adm:topup", "adm:extra", "adm:utm"}))
async def stub_sections(callback: CallbackQuery):
    await callback.answer("🚧 Раздел в разработке", show_alert=True)


# ========= СТАТИСТИКА =========
@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery):
    st = await get_stats()
    s = await get_settings()
    await callback.answer()

    plans_lines = "\n".join(
        f"⏰ {p.name}: {st['plan_counts'].get(p.key, 0)}"
        for p in st["plans"]
    )
    await _render(
        callback.message,
        f"📊 <b>Статистика бота {s.bot_name}</b>\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"Всего: <b>{st['total']}</b>\n"
        f"Новых за день: <b>{st['new_day']}</b> | "
        f"за неделю: <b>{st['new_week']}</b> | "
        f"за месяц: <b>{st['new_month']}</b>\n\n"
        f"🔑 <b>Активных подписок: {st['subs_total']}</b>\n"
        f"💎 Платных (3+ дня): <b>{st['subs_paid']}</b>\n"
        f"📅 Подписки по тарифам:\n{plans_lines}\n\n"
        f"💰 <b>Пополнения и покупки</b>\n"
        f"Покупок подписок: <b>{st['buys_count']}</b> на <b>{fmt_money(st['buys_sum'])} ₽</b>\n"
        f"Пополнений баланса: <b>{st['topups_count']}</b> на <b>{fmt_money(st['topups_sum'])} ₽</b>",
        back_kb(),
    )


# ========= ПОЛЬЗОВАТЕЛИ И ПОДПИСКИ =========
@router.callback_query(F.data == "adm:users")
async def users_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render(callback.message, "👥 <b>Пользователи и подписки</b>", users_menu_kb())


@router.callback_query(F.data == "usr:menu")
async def users_menu_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render(callback.message, "👥 <b>Пользователи и подписки</b>", users_menu_kb())


@router.callback_query(F.data == "usr:list")
async def users_list(callback: CallbackQuery):
    async with SessionMaker() as session:
        users = (await session.scalars(
            select(User).order_by(User.created_at.desc()).limit(10)
        )).all()

    await callback.answer()
    if not users:
        await _render(callback.message, "📋 Пока пусто.", users_menu_kb())
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
    await _render(callback.message, f"📋 <b>Последние юзеры:</b>\n\n{text}", kb.as_markup())


async def _user_card_text(u: User) -> str:
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

    if sub:
        p = await get_plan(sub.plan)
        sub_line = f"{p.name if p else sub.plan}, до {sub.expires_at:%d.%m.%Y}"
    else:
        sub_line = "нет"

    return (
        f"👤 <code>{u.id}</code> — @{u.username or '—'}\n"
        f"📅 Регистрация: <b>{u.created_at:%d.%m.%Y}</b>\n"
        f"💎 Подписка: <b>{sub_line}</b>\n"
        f"💰 Платежей на: <b>{fmt_money(float(paid))} ₽</b>\n"
        f"{'🚫 ЗАБАНЕН' if u.is_banned else '✅ Активен'}"
    )


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
    await _render(callback.message, await _user_card_text(u), user_card_kb(u.id, u.is_banned))


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
    try:
        await message.delete()
    except Exception:
        pass
    if not u:
        await message.answer("❌ Не найден.", reply_markup=users_menu_kb())
        return
    await message.answer(await _user_card_text(u), reply_markup=user_card_kb(u.id, u.is_banned))


# ========= ВЫДАЧА ПОДПИСКИ ВРУЧНУЮ =========
@router.callback_query(F.data.startswith("usr:grant:"))
async def grant_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":")[2])
    await state.set_state(UserStates.grant)
    await state.update_data(uid=uid)
    await callback.answer()
    await _render(
        callback.message,
        f"🎁 Выдаём подписку юзеру <code>{uid}</code>.\nВыбери тариф:",
        await plan_kb(),
    )


@router.callback_query(UserStates.grant, F.data.startswith("usr:plan:"))
async def grant_apply(callback: CallbackQuery, state: FSMContext):
    plan_key = callback.data.split(":")[2]
    plan = await get_plan(plan_key)
    if not plan:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    data = await state.get_data()
    uid = data["uid"]
    now = _utcnow()

    async with SessionMaker() as session:
        current = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == uid, Subscription.expires_at > now)
            .order_by(Subscription.expires_at.desc())
        )
        base = current.expires_at if current else now
        expires = base + __import__("datetime").timedelta(days=plan.days)
        session.add(Subscription(user_id=uid, plan=plan.key, created_at=now, expires_at=expires))
        await session.commit()

    await state.clear()
    await callback.answer("✅ Выдано", show_alert=True)
    await callback.message.answer(
        f"✅ <b>{plan.name}</b> выдана юзеру <code>{uid}</code>\n"
        f"Действует до: <b>{expires:%d.%m.%Y}</b>"
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
    await _render(callback.message, await _user_card_text(u), user_card_kb(uid, banned))


# ========= КАСТОМИЗАЦИЯ =========
async def _render_custom(msg: Message):
    s = await get_settings()
    plans = await get_plans()
    prices_lines = "\n".join(f"• {p.name} — {fmt_money(float(p.price))}₽" for p in plans)
    trial = f"включена, {s.trial_days} дн. · бесплатно" if s.trial_enabled else "выключена"
    await _render(
        msg,
        f"⚙️ <b>Кастомизация (конфиг)</b>\n\n"
        f"✏️ Название: <b>{s.bot_name}</b>\n"
        f"💰 Базовые цены:\n{prices_lines}\n"
        f"📢 Канал: <b>{s.channel_url or '—'}</b>\n"
        f"👨‍💻 Поддержка: <b>{s.support_url or '—'}</b>\n"
        f"🔔 Уведомления: <b>{s.logs_channel or '—'}</b>\n"
        f"🕒 Часовой пояс: <b>{s.timezone_offset}</b>\n"
        f"🎁 Пробная подписка: <b>{trial}</b>",
        custom_menu_kb(s),
    )


@router.callback_query(F.data == "adm:custom")
async def admin_custom(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render_custom(callback.message)


@router.callback_query(F.data == "cus:back")
async def cus_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render_custom(callback.message)


@router.callback_query(F.data == "cus:stub")
async def cus_stub(callback: CallbackQuery):
    await callback.answer("🚧 Раздел в разработке", show_alert=True)


async def _prompt(callback: CallbackQuery, state: FSMContext, new_state, text: str):
    await state.set_state(new_state)
    await callback.answer()
    await callback.message.answer(f"{text}\n\n<i>Отменить: /cancel</i>")


async def _save_text_field(message: Message, state: FSMContext, field: str, label: str, maxlen: int = 256):
    value = message.text.strip()[:maxlen]
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        setattr(s, field, value)
        await session.commit()
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(f"✅ {label}: <b>{value or '—'}</b>")


@router.callback_query(F.data == "cus:name")
async def cus_name(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.name, "✏️ Отправь новое название бота:")


@router.callback_query(F.data == "cus:locations")
async def cus_locations(callback: CallbackQuery, state: FSMContext):
    await _prompt(
        callback, state, CustomStates.locations,
        "🌎 Отправь локации — каждая с новой строки:\n\n"
        "<code>⚡ Быстрый | АВТО\n🌍 Остальные | АВТО\n📱 Мобильный | АВТО</code>",
    )


@router.callback_query(F.data == "cus:channel")
async def cus_channel(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.channel, "📢 Отправь ссылку на канал (@username или https://t.me/...):")


@router.callback_query(F.data == "cus:support")
async def cus_support(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.support, "👨‍💻 Отправь ссылку на поддержку:")


@router.callback_query(F.data == "cus:logs")
async def cus_logs(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.logs, "🔕 Отправь ID канала уведомлений (-100...):")


@router.callback_query(F.data == "cus:tz")
async def cus_tz(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.tz, "🕓 Отправь часовой пояс, например <code>UTC+03:00</code>:")


@router.message(CustomStates.name, F.text)
async def save_name(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.bot_name = message.text.strip()[:64]
        await session.commit()
        saved = s.bot_name
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(f"✅ Название сохранено: <b>{saved}</b>")


@router.message(CustomStates.locations, F.text)
async def save_locations(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.locations = message.text.strip()
        await session.commit()
        count = len(s.locations.splitlines())
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
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


@router.message(CustomStates.channel, F.text)
async def save_channel(message: Message, state: FSMContext):
    await _save_text_field(message, state, "channel_url", "📢 Канал")


@router.message(CustomStates.support, F.text)
async def save_support(message: Message, state: FSMContext):
    await _save_text_field(message, state, "support_url", "👨‍💻 Поддержка")


@router.message(CustomStates.logs, F.text)
async def save_logs(message: Message, state: FSMContext):
    await _save_text_field(message, state, "logs_channel", "🔕 Уведомления", maxlen=64)


@router.message(CustomStates.tz, F.text)
async def save_tz(message: Message, state: FSMContext):
    await _save_text_field(message, state, "timezone_offset", "🕓 Часовой пояс", maxlen=16)


# ========= ЦЕНЫ =========
@router.callback_query(F.data == "cus:prices")
async def cus_prices(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    plans = await get_plans()
    lines = "\n".join(f"• {p.name} — <b>{fmt_money(float(p.price))}₽</b>" for p in plans)
    await callback.answer()
    await _render(
        callback.message,
        f"💰 <b>Базовые цены</b>\n\n{lines}\n\nВыбери тариф для изменения:",
        await prices_menu_kb(),
    )


@router.callback_query(F.data.startswith("cus:price:"))
async def cus_price(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]
    plan = await get_plan(key)
    if not plan:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    await state.set_state(CustomStates.price)
    await state.update_data(plan_key=key)
    await callback.answer()
    await callback.message.answer(
        f"💰 Тариф <b>{plan.name}</b>.\nОтправь новую цену в рублях (просто число, например <code>250</code>):\n\n<i>Отменить: /cancel</i>"
    )


@router.message(CustomStates.price, F.text)
async def save_price(message: Message, state: FSMContext):
    raw = message.text.strip().replace("₽", "").replace(" ", "")
    if not raw.replace(".", "", 1).isdigit():
        await message.answer("⚠️ Нужна цена числом, например <code>250</code>. Попробуй ещё раз:")
        return

    data = await state.get_data()
    async with SessionMaker() as session:
        p = await session.get(Plan, data["plan_key"])
        p.price = float(raw)
        await session.commit()
        name = p.name
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    plans = await get_plans()
    lines = "\n".join(f"• {pl.name} — <b>{fmt_money(float(pl.price))}₽</b>" for pl in plans)
    await message.answer(
        f"✅ Цена обновлена: <b>{name}</b>\n\n💰 <b>Базовые цены</b>\n{lines}",
        reply_markup=await prices_menu_kb(),
    )


# ========= ПРОБНАЯ ПОДПИСКА =========
def _trial_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Вкл/Выкл", callback_data="cus:trial:toggle")
    kb.button(text="✏️ Изменить дни", callback_data="cus:trial:days")
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(1)
    return kb.as_markup()


def _trial_text(s: Settings) -> str:
    status = f"включена, {s.trial_days} дн. · бесплатно" if s.trial_enabled else "выключена"
    return f"🎁 <b>Пробная подписка</b>\n\nСтатус: <b>{status}</b>"


@router.callback_query(F.data == "cus:trial")
async def cus_trial(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    await callback.answer()
    await _render(callback.message, _trial_text(s), _trial_kb())


@router.callback_query(F.data == "cus:trial:toggle")
async def cus_trial_toggle(callback: CallbackQuery):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.trial_enabled = not s.trial_enabled
        await session.commit()
        enabled, days = s.trial_enabled, s.trial_days
    await callback.answer("✅ Включена" if enabled else "❌ Выключена", show_alert=True)
    await _render(callback.message, _trial_text(await get_settings()), _trial_kb())


@router.callback_query(F.data == "cus:trial:days")
async def cus_trial_days(callback: CallbackQuery, state: FSMContext):
    await _prompt(callback, state, CustomStates.trial_days, "✏️ Отправь длительность пробной подписки в днях (число):")


@router.message(CustomStates.trial_days, F.text)
async def save_trial_days(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 30):
        await message.answer("⚠️ Нужно число от 1 до 30. Попробуй ещё раз:")
        return
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.trial_days = int(raw)
        await session.commit()
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(_trial_text(await get_settings()), reply_markup=_trial_kb())


# ========= ТУМБЛЕРЫ =========
@router.callback_query(F.data.startswith("cus:tgl:"))
async def cus_toggle(callback: CallbackQuery):
    field = callback.data.split(":")[2]
    allowed = {"roulette_enabled", "gifts_enabled", "marketing_enabled"}
    if field not in allowed:
        await callback.answer("⛔ Недоступно", show_alert=True)
        return
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        setattr(s, field, not getattr(s, field))
        await session.commit()
    await callback.answer("✅ Переключено")
    await _render_custom(callback.message)


# ========= РАССЫЛКА =========
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
    data = await state.get_data()
    await state.clear()
    await callback.answer("❌ Отменено")
    if data.get("chat_id") and data.get("message_id"):
        try:
            await callback.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
        except Exception:
            pass
    await callback.message.delete()
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
            await bot.copy_message(chat_id=uid, from_chat_id=data["chat_id"], message_id=data["message_id"])
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    try:
        await bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass
    await status.delete()

    await callback.message.answer(
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