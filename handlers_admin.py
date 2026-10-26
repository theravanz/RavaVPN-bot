import asyncio
from datetime import timedelta

import aiohttp
from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from config import settings
from database import (
    MIN_PRICES, Payment, Plan, SessionMaker, Settings, Subscription, User,
    _utcnow, fmt_money, get_admin_summary, get_plan, get_plans,
    get_settings, get_stats,
)
from filters import IsAdmin
from keyboards import (
    admin_main_kb, back_kb, confirm_kb, custom_menu_kb, main_menu_keyboard,
    marketing_kb, plan_kb, prices_menu_kb, trial_kb, tz_kb,
    user_card_kb, users_menu_kb,
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


class BroadcastStates(StatesGroup):
    waiting = State()
    confirm = State()


class UserStates(StatesGroup):
    search = State()
    grant = State()


CANCEL_HINT = "\n\n<i>Для отмены отправьте -</i>"


# ========= ШАПКА ПАНЕЛИ =========
MODES = [
    ("hybrid", "🔀 Гибрид — бот и сайт работают вместе"),
    ("assistant", "🌐 Помощник сайту — только вход по коду"),
    ("autonomous", "🤖 Автономный — сайт не участвует"),
]


async def _panel_text() -> str:
    s = await get_settings()
    st = await get_admin_summary()
    mode_label = dict(MODES).get(getattr(s, "bot_mode", "hybrid"), "hybrid")
    return (
        f"🛠 <b>Панель управления франшизой {s.bot_name}</b>\n\n"
        f"🧭 <b>Режим работы:</b> {mode_label}\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"👥 Пользователей: <b>{st['total']}</b> (+{st['new_today']})\n"
        f"💎 Активных подписок: <b>{st['subs_active']}</b>\n"
        f"💰 Оборот бота: <b>{fmt_money(st['turnover'])} ₽</b>\n"
        f"🛒 Продаж сегодня: <b>{fmt_money(st['sales_today'])} ₽</b>\n\n"
        f"💰 <b>ВАШ БАЛАНС:</b> <b>{fmt_money(float(s.balance))} ₽</b>\n\n"
        f"🌐 Веб-панель сайта — кнопка ниже 👇"
    )


async def _render(target: Message, text: str, kb, *, also_delete: Message | None = None):
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
@router.callback_query(F.data == "adm:mode")
async def admin_mode_cycle(callback: CallbackQuery):
    """Циклическое переключение режима работы бота."""
    s = await get_settings()
    current = getattr(s, "bot_mode", "hybrid")
    idx = next((i for i, (k, _) in enumerate(MODES) if k == current), 0)
    new_key, new_label = MODES[(idx + 1) % len(MODES)]

    async with SessionMaker() as session:
        st = await session.get(Settings, 1)
        if st:
            st.bot_mode = new_key
            await session.commit()

    await callback.answer(f"Режим: {new_label}", show_alert=True)
    await _render(callback.message, await _panel_text(), admin_main_kb())


@router.callback_query(F.data.in_({"adm:webpanel", "adm:webpanel_reset"}))
async def admin_webpanel(callback: CallbackQuery):
    """Секретная ссылка веб-панели: показать или сбросить (через сайт)."""
    reset = callback.data.endswith("_reset")
    api = f"{settings.web_api_url}/api/admin/link{'?reset=1' if reset else ''}"
    headers = {"x-bot-secret": settings.telegram_auth_secret, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(api, json={}, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("url"):
                    text = (
                        "♻️ Ссылка <b>сброшена</b> — старая больше не работает.\n\n"
                        f"🔗 Новая веб-панель:\n<code>{data['url']}</code>\n\n"
                        "Держите адрес в тайне и не пересылайте никому."
                        if reset else
                        "🔗 <b>Веб-панель сайта</b> (статистика, участники, бонусы):\n"
                        f"<code>{data['url']}</code>\n\n"
                        "Держите адрес в тайне. Сбросить можно здесь же или в самой панели."
                    )
                    await _render(callback.message, text, admin_main_kb())
                else:
                    await callback.answer(f"Сайт: {data.get('error', resp.status)}", show_alert=True)
    except Exception as e:
        await callback.answer(f"Сайт недоступен: {e}", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery):
    st = await get_stats()
    s = await get_settings()
    await callback.answer()

    plans_lines = "\n".join(
        f"⏰ {p.name}: {st['plan_counts'].get(p.key, 0)}" for p in st["plans"]
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
@router.callback_query(F.data.in_({"adm:users", "usr:menu"}))
async def users_menu(callback: CallbackQuery, state: FSMContext):
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
        "🔍 Отправь <b>ID</b> юзера или его <b>@username</b>." + CANCEL_HINT,
        reply_markup=back_kb("usr:menu"),
    )


@router.message(UserStates.search)
async def users_search_do(message: Message, state: FSMContext):
    query = (message.text or "").strip().lstrip("@")
    if query == "-":
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("👥 <b>Пользователи и подписки</b>", reply_markup=users_menu_kb())
        return

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
        plan_kb(await get_plans()),
    )


@router.callback_query(UserStates.grant, F.data.startswith("usr:plan:"))
async def grant_apply(callback: CallbackQuery, state: FSMContext):
    plan = await get_plan(callback.data.split(":")[2])
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
        expires = base + timedelta(days=plan.days)
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
        f"📱 Устройство: <b>{fmt_money(float(s.device_price_month))}₽/мес</b> · "
        f"<b>{fmt_money(float(s.device_price_day))}₽/день</b>\n"
        f"📢 Канал: <b>{s.channel_url or '—'}</b>\n"
        f"👨‍💻 Поддержка: <b>{s.support_url or '—'}</b>\n"
        f"🔔 Уведомления: <b>{('ID ' + s.logs_channel) if s.logs_channel else 'Личные сообщения'}</b>\n"
        f"🕒 Часовой пояс: <b>{s.timezone_offset}</b>\n"
        f"🎁 Пробная подписка: <b>{trial}</b>",
        custom_menu_kb(s),
    )


@router.callback_query(F.data.in_({"adm:custom", "cus:back"}))
async def admin_custom(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render_custom(callback.message)


@router.callback_query(F.data == "cus:stub")
async def cus_stub(callback: CallbackQuery):
    await callback.answer("🚧 Раздел в разработке", show_alert=True)


async def _prompt(callback: CallbackQuery, state: FSMContext, new_state, text: str):
    await state.set_state(new_state)
    await callback.answer()
    await callback.message.answer(text + CANCEL_HINT, reply_markup=back_kb("cus:back"))


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


# ---- название / локации / фото ----
@router.message(CustomStates.name, F.text)
async def save_name(message: Message, state: FSMContext):
    if message.text.strip() == "-":
        await state.clear()
        return await _after_cancel_input(message)
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.bot_name = message.text.strip()[:64]
        await session.commit()
        saved = s.bot_name
    await state.clear()
    await _delete_and_confirm(message, f"✅ Название сохранено: <b>{saved}</b>")


@router.message(CustomStates.locations, F.text)
async def save_locations(message: Message, state: FSMContext):
    if message.text.strip() == "-":
        await state.clear()
        return await _after_cancel_input(message)
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.locations = message.text.strip()
        await session.commit()
        count = len(s.locations.splitlines())
    await state.clear()
    await _delete_and_confirm(message, f"✅ Локации сохранены ({count} шт.)")


@router.message(CustomStates.photo, F.photo)
async def save_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.main_menu_photo_id = file_id
        await session.commit()
    await state.clear()
    await message.answer("✅ Фото сохранено, проверь /start")


# ---- ЦЕНЫ ----
def _prices_overview(plans, s: Settings) -> str:
    mins = "\n".join(
        f"• {await_plan_name(key)} — {fmt_money(price)}₽"
        for key, price in MIN_PRICES.items()
    )
    return (
        f"💰 <b>Выберите тариф для изменения цены</b>\n\n"
        f"<b>Минимальные суммы:</b>\n"
        f"• 1 месяц — {fmt_money(MIN_PRICES['1m'])}₽\n"
        f"• 3 месяца — {fmt_money(MIN_PRICES['3m'])}₽\n"
        f"• 6 месяцев — {fmt_money(MIN_PRICES['6m'])}₽\n"
        f"• 1 год — {fmt_money(MIN_PRICES['12m'])}₽\n"
        f"• Устройство при покупке/продлении — "
        f"{fmt_money(MIN_PRICES.get('dev_month', 0))}₽/мес (сейчас: {fmt_money(float(s.device_price_month))}₽)\n"
        f"• Устройство при добавлении к активной — "
        f"{fmt_money(MIN_PRICES.get('dev_day', 0))}₽/день (сейчас: {fmt_money(float(s.device_price_day))}₽)"
    )


async def _plan_name(key: str) -> str:
    p = await get_plan(key)
    return p.name if p else key


@router.callback_query(F.data == "cus:prices")
async def cus_prices(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    plans = [p for p in await get_plans() if p.key in MIN_PRICES]
    s = await get_settings()
    mins = (
        f"<b>Минимальные суммы:</b>\n"
        f"• 1 месяц — {fmt_money(MIN_PRICES['1m'])}₽\n"
        f"• 3 месяца — {fmt_money(MIN_PRICES['3m'])}₽\n"
        f"• 6 месяцев — {fmt_money(MIN_PRICES['6m'])}₽\n"
        f"• 1 год — {fmt_money(MIN_PRICES['12m'])}₽\n"
        f"• Устройство при покупке/продлении — {fmt_money(float(s.device_price_month))}₽/мес\n"
        f"• Устройство при добавлении к активной — {fmt_money(float(s.device_price_day))}₽/день"
    )
    await callback.answer()
    await _render(
        callback.message,
        f"💰 <b>Выберите тариф для изменения цены</b>\n\n{mins}",
        prices_menu_kb(plans),
    )


@router.callback_query(F.data.startswith("cus:price:"))
async def cus_price(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]

    if key == "dev_month":
        s = await get_settings()
        await state.set_state(CustomStates.price)
        await state.update_data(kind="dev_month")
        await callback.answer()
        await callback.message.answer(
            f"📱 <b>Устройство при покупке/продлении</b>.\n"
            f"Сейчас: <b>{fmt_money(float(s.device_price_month))}₽/мес</b>.\n"
            f"Отправь новую цену в рублях за месяц:" + CANCEL_HINT,
            reply_markup=back_kb("cus:back"),
        )
        return
    if key == "dev_day":
        s = await get_settings()
        await state.set_state(CustomStates.price)
        await state.update_data(kind="dev_day")
        await callback.answer()
        await callback.message.answer(
            f"➕ <b>Устройство при добавлении к активной подписке</b>.\n"
            f"Сейчас: <b>{fmt_money(float(s.device_price_day))}₽/день</b>.\n"
            f"Отправь новую цену в рублях за день:" + CANCEL_HINT,
            reply_markup=back_kb("cus:back"),
        )
        return

    plan = await get_plan(key)
    if not plan:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    min_p = MIN_PRICES.get(key, 0)
    await state.set_state(CustomStates.price)
    await state.update_data(kind="plan", plan_key=key)
    await callback.answer()
    await callback.message.answer(
        f"💰 Тариф <b>{plan.name}</b>.\n"
        f"Минимальная цена: <b>{fmt_money(min_p)}₽</b>.\n"
        f"Отправь новую цену (просто число, например <code>{min_p}</code>):" + CANCEL_HINT,
        reply_markup=back_kb("cus:back"),
    )


@router.message(CustomStates.price, F.text)
async def save_price(message: Message, state: FSMContext):
    raw = message.text.strip().replace("₽", "").replace(" ", "")
    if raw == "-":
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        return await _after_cancel_input(message)
    if not raw.replace(".", "", 1).isdigit():
        await message.answer("⚠️ Нужна цена числом, например <code>250</code>. Попробуй ещё раз:")
        return
    value = float(raw)

    data = await state.get_data()
    kind = data.get("kind")

    if kind == "dev_month":
        async with SessionMaker() as session:
            s = await session.get(Settings, 1)
            s.device_price_month = value
            await session.commit()
        label = f"📱 Устройство при покупке/продлении: <b>{fmt_money(value)}₽/мес</b>"
    elif kind == "dev_day":
        async with SessionMaker() as session:
            s = await session.get(Settings, 1)
            s.device_price_day = value
            await session.commit()
        label = f"➕ Устройство при добавлении к активной: <b>{fmt_money(value)}₽/день</b>"
    else:
        key = data["plan_key"]
        min_p = MIN_PRICES.get(key, 0)
        if value < min_p:
            await message.answer(
                f"⚠️ Минимальная цена для этого тарифа — <b>{fmt_money(min_p)}₽</b>.\nПопробуй ещё раз:"
            )
            return
        async with SessionMaker() as session:
            p = await session.get(Plan, key)
            p.price = value
            await session.commit()
            label = f"✅ Цена обновлена: <b>{p.name}</b> — <b>{fmt_money(value)}₽</b>"

    await state.clear()
    await _delete_and_confirm(message, label)


# ---- МАРКЕТИНГ ----
def _marketing_text(s: Settings) -> str:
    status = "Включены" if s.marketing_enabled else "Выключены"
    return (
        f"📨 <b>Маркетинговые сообщения</b>\n\n"
        f"Статус: <b>{status}</b>\n\n"
        f"Одноразовая скидка 20% на 1 месяц, действует 3 дня. Предложение отправляется через 3 дня "
        f"после пробной подписки, если покупок не было, или через 7 дней после платной подписки.\n\n"
        f"После включения отправка начинается через час, включая подходящих прежних пользователей. "
        f"Выключение останавливает новые сообщения; отправленные предложения действуют до срока.\n\n"
        f"<b>Предложение зарегистрировано:</b> 0\n"
        f"<b>Покупки со скидкой:</b> 0\n"
        f"<b>Срок предложения истёк:</b> 0\n"
        f"<b>Доставка не подтверждена:</b> 0\n"
        f"<b>Бот недоступен пользователю:</b> 0"
    )


@router.callback_query(F.data == "cus:marketing")
async def cus_marketing(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    await callback.answer()
    await _render(callback.message, _marketing_text(s), marketing_kb(s.marketing_enabled))


@router.callback_query(F.data == "cus:marketing:toggle")
async def cus_marketing_toggle(callback: CallbackQuery):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.marketing_enabled = not s.marketing_enabled
        await session.commit()
        enabled = s.marketing_enabled
    await callback.answer("✅ Включены" if enabled else "❌ Выключены", show_alert=True)
    await _render(callback.message, _marketing_text(await get_settings()), marketing_kb(enabled))


# ---- ПРОБНАЯ ПОДПИСКА ----
def _trial_text(s: Settings) -> str:
    status = "Включена" if s.trial_enabled else "Выключена"
    return (
        f"🎁 <b>Пробная подписка</b>\n\n"
        f"Цена: <b>Бесплатно</b>\n"
        f"Статус: <b>{status}</b>\n"
        f"Срок: <b>{s.trial_days} дн.</b>\n\n"
        f"Выберите срок новых пробных подписок:"
    )


@router.callback_query(F.data == "cus:trial")
async def cus_trial(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    await callback.answer()
    await _render(callback.message, _trial_text(s), trial_kb(s.trial_enabled, s.trial_days))


@router.callback_query(F.data == "cus:trial:toggle")
async def cus_trial_toggle(callback: CallbackQuery):
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.trial_enabled = not s.trial_enabled
        await session.commit()
        enabled, days = s.trial_enabled, s.trial_days
    await callback.answer("✅ Включена" if enabled else "❌ Выключена", show_alert=True)
    await _render(callback.message, _trial_text(await get_settings()), trial_kb(enabled, days))


@router.callback_query(F.data.startswith("cus:trial:days:"))
async def cus_trial_days(callback: CallbackQuery):
    days = int(callback.data.split(":")[3])
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.trial_days = days
        await session.commit()
    await callback.answer(f"✅ Срок: {days} дн.", show_alert=True)
    await _render(callback.message, _trial_text(await get_settings()), trial_kb(True, days))


# ---- КАНАЛ / ПОДДЕРЖКА / УВЕДОМЛЕНИЯ ----
@router.callback_query(F.data == "cus:channel")
async def cus_channel(callback: CallbackQuery, state: FSMContext):
    s = await get_settings()
    await state.set_state(CustomStates.channel)
    await callback.answer()
    await callback.message.answer(
        f"📢 <b>Канал новостей</b>\n"
        f"Текущая настройка: <b>{s.channel_url or '—'}</b>\n\n"
        f"Отправьте ID канала, публичную или приватную ссылку, либо перешлите сообщение из канала. "
        f"Для удаления отправьте <code>-</code>.\n\n"
        f"<i>Не забудьте добавить вашего бота в админы канала!</i>" + CANCEL_HINT,
        reply_markup=back_kb("cus:back"),
    )


@router.message(CustomStates.channel, F.text)
async def save_channel_text(message: Message, state: FSMContext):
    val = message.text.strip()
    if val == "-":
        val = ""
    elif message.forward_from_chat:
        val = str(message.forward_from_chat.id)
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.channel_url = val[:256]
        await session.commit()
    await state.clear()
    await _delete_and_confirm(message, f"📢 Канал: <b>{val or '— (удалён)'}</b>")


@router.message(CustomStates.channel, F.forward_from_chat)
async def save_channel_fwd(message: Message, state: FSMContext):
    chat_id = str(message.forward_from_chat.id)
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.channel_url = chat_id
        await session.commit()
    await state.clear()
    await _delete_and_confirm(message, f"📢 Канал: <b>{chat_id}</b>")


@router.callback_query(F.data == "cus:support")
async def cus_support(callback: CallbackQuery, state: FSMContext):
    s = await get_settings()
    await state.set_state(CustomStates.support)
    await callback.answer()
    await callback.message.answer(
        f"👨‍💻 <b>Техподдержка</b>\n"
        f"Текущая настройка: <b>{s.support_url or '—'}</b>\n\n"
        f"Отправьте ссылку на поддержку. Для удаления отправьте <code>-</code>." + CANCEL_HINT,
        reply_markup=back_kb("cus:back"),
    )


@router.message(CustomStates.support, F.text)
async def save_support(message: Message, state: FSMContext):
    val = message.text.strip()
    if val == "-":
        val = ""
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.support_url = val[:256]
        await session.commit()
    await state.clear()
    await _delete_and_confirm(message, f"👨‍💻 Поддержка: <b>{val or '— (удалена)'}</b>")


@router.callback_query(F.data == "cus:logs")
async def cus_logs(callback: CallbackQuery, state: FSMContext):
    s = await get_settings()
    await state.set_state(CustomStates.logs)
    await callback.answer()
    current = f"ID {s.logs_channel}" if s.logs_channel else "Личные сообщения бота"
    await callback.message.answer(
        f"🔔 <b>Канал уведомлений</b>\n"
        f"Текущая настройка: <b>{current}</b>\n\n"
        f"Отправьте новый ID канала/группы (например: <code>-100123456789</code>).\n"
        f"Или отправьте <code>0</code>, чтобы получать уведомления в личные сообщения этого бота.\n"
        f"Для удаления отправьте <code>-</code>." + CANCEL_HINT,
        reply_markup=back_kb("cus:back"),
    )


@router.message(CustomStates.logs, F.text)
async def save_logs(message: Message, state: FSMContext):
    raw = message.text.strip()
    if raw == "-":
        val = ""
    elif raw == "0":
        val = ""  # 0 = ЛС
    else:
        val = raw
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.logs_channel = val[:64]
        await session.commit()
    await state.clear()
    current = f"ID {val}" if val else "Личные сообщения"
    await _delete_and_confirm(message, f"🔔 Уведомления: <b>{current}</b>")


# ---- ЧАСОВОЙ ПОЯС ----
@router.callback_query(F.data == "cus:tz")
async def cus_tz(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    await callback.answer()
    await _render(
        callback.message,
        f"🕓 <b>Текущий часовой пояс: {s.timezone_offset}</b>\n\nВыберите ваш часовой пояс:",
        tz_kb(s.timezone_offset),
    )


@router.callback_query(F.data.startswith("cus:tz:"))
async def cus_tz_set(callback: CallbackQuery):
    h = int(callback.data.split(":")[2])
    label = f"UTC{h:+03d}:00"
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        s.timezone_offset = label
        await session.commit()
    await callback.answer(f"✅ {label}", show_alert=True)
    await _render(
        callback.message,
        f"🕓 <b>Текущий часовой пояс: {label}</b>\n\nВыберите ваш часовой пояс:",
        tz_kb(label),
    )


# ---- ТУМБЛЕРЫ ----
@router.callback_query(F.data.startswith("cus:tgl:"))
async def cus_toggle(callback: CallbackQuery):
    field = callback.data.split(":")[2]
    allowed = {"roulette_enabled", "gifts_enabled"}
    if field not in allowed:
        await callback.answer("⛔ Недоступно", show_alert=True)
        return
    async with SessionMaker() as session:
        s = await session.get(Settings, 1)
        setattr(s, field, not getattr(s, field))
        await session.commit()
    await callback.answer("✅ Переключено")
    await _render_custom(callback.message)


# ---- хелперы ----
async def _after_cancel_input(message: Message):
    """После '-' в FSM — показываем кастомизацию заново."""
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ Отменено.")
    s = await get_settings()
    plans = await get_plans()
    prices_lines = "\n".join(f"• {p.name} — {fmt_money(float(p.price))}₽" for p in plans)
    trial = f"включена, {s.trial_days} дн." if s.trial_enabled else "выключена"
    await message.answer(
        f"⚙️ <b>Кастомизация (конфиг)</b>\n\n"
        f"✏️ Название: <b>{s.bot_name}</b>\n"
        f"💰 Базовые цены:\n{prices_lines}\n"
        f"🎁 Пробная подписка: <b>{trial}</b>",
        reply_markup=custom_menu_kb(s),
    )


async def _delete_and_confirm(message: Message, text: str):
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(text)


# ========= РАССЫЛКА =========
@router.callback_query(F.data == "adm:broadcast")
async def bc_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting)
    await callback.answer()
    await callback.message.answer(
        "📢 Отправь сообщение для рассылки — текст, фото, видео, что угодно." + CANCEL_HINT,
        reply_markup=back_kb("adm:main"),
    )


@router.message(BroadcastStates.waiting, F.text == "-")
async def bc_cancel_input(message: Message, state: FSMContext):
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ Отменено.", reply_markup=admin_main_kb())


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


# ========= /cancel (оставил как запасной путь) =========
@router.message(F.text == "/cancel")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Отменено.", reply_markup=admin_main_kb())