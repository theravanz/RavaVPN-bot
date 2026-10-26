import re

import aiohttp
from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import (
    SessionMaker, User,
    attach_referral, ensure_referral_code, find_user_by_ref_code,
    get_plan, get_plans, get_settings, get_user, upsert_user,
)
from keyboards import main_menu_keyboard
from texts import get_main_menu_text, get_stub_text
from web_client import fetch_web_stats, web_post

router = Router()

AUTH_CODE_RE = re.compile(r"^[0-9a-f]{48}$")  # randomBytes(24) от сайта

TG_SUPPORT_URL = "https://t.me/ravavpnhelp"
TG_SUPPORT_LABEL = "Написать в поддержку"

PAYMENT_METHODS = [
    ("sbp", "🅿️ Platega · СБП"),
    ("crypto", "🪙 Platega · Криптовалюта"),
    ("cispay", "💳 CISPay"),
]

SECTION_NAMES = {
    "subscribe": "Подписка",
    "referral": "Реферальная программа",
    "roulette": "Рулетка",
    "promo": "Промокод",
    "info": "INFO",
    "own_vpn": "Хочу свой VPN",
    "lang": "Change language",
}


async def send_main_menu(message: Message):
    s = await get_settings()
    kb = main_menu_keyboard(s) 

    if s.main_menu_photo_id:
        try:
            await message.answer_photo(
                photo=s.main_menu_photo_id,
                caption=get_main_menu_text(s),
                reply_markup=kb,
            )
            return
        except Exception:
            pass

    await message.answer(text=get_main_menu_text(s), reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    args = command.args if command else None
    if args and AUTH_CODE_RE.match(args):
        # payload — код авторизации на сайте (24 hex)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Да, войти", callback_data=f"auth:yes:{args}")
        kb.button(text="❌ Нет, отменить", callback_data=f"auth:no:{args}")
        kb.adjust(2)

        await message.answer(
            text=f"🔐 <b>Подтвердите авторизацию на сайте</b>\n\nКод входа: <code>{args[:8]}...</code>\n\nВы запрашивали вход в RavaVPN?",
            reply_markup=kb.as_markup(),
        )
        return

    await upsert_user(message.from_user.id, message.from_user.username)

    if args and args.startswith("ref_"):
        await handle_referral_start(message, args)

    await send_main_menu(message)


async def handle_referral_start(message: Message, ref_code: str):
    """Друг пришёл по реферальной ссылке: локально привязываем + событие на сайт (outbox)."""
    tg_id = message.from_user.id
    username = message.from_user.username

    inviter = await find_user_by_ref_code(ref_code)
    if not inviter:
        await message.answer("⚠️ Ссылка недействительна — пригласивший не найден.")
        return

    attached = await attach_referral(tg_id, username, inviter.id)
    if attached:
        await web_post(
            "referral_attach",
            "/api/referrals/event",
            {"tgId": str(tg_id), "tgUsername": username, "refCode": ref_code},
        )
        await message.answer(
            f"🎉 Вас пригласил <b>{('@' + inviter.username) if inviter.username else 'друг'}</b>! "
            "Приятного пользования RavaVPN."
        )
    # повторный переход по чужой ссылке — просто игнорируем (уже приглашён)


@router.callback_query(F.data == "menu:referral")
async def referral_menu(callback: CallbackQuery):
    """Реферальная программа: код и баланс из локального кэша, статистика — best-effort с сайта."""
    await callback.answer()
    tg_id = callback.from_user.id

    # Единый источник истины — сайт: берём его код, локальный только как запасной
    stats = await fetch_web_stats(tg_id)
    if stats:
        code = stats["refCode"]
        # сохраняем сайт-код в локальное зеркало, чтобы он же ушёл в пригласительные события
        await ensure_referral_code(tg_id, callback.from_user.username)
        async with SessionMaker() as session:
            u2 = await session.get(User, tg_id)
            if u2 and u2.referral_code != code:
                u2.referral_code = code
                await session.commit()
    else:
        code = await ensure_referral_code(tg_id, callback.from_user.username)

    bot_link = f"https://t.me/{settings.bot_username}?start={code}"
    u = await get_user(tg_id)

    balance = float(u.referral_balance or 0) if u else 0.0
    invited_note = ""

    if stats:
        balance = stats["balance"] / 100
        invited_note = f"\n👥 Приглашено: <b>{stats['invited']}</b> · оплатили: <b>{stats['connected']}</b>"
    else:
        invited_note = "\n👥 Статистика появится, когда сайт будет на связи."

    await callback.message.answer(
        text=(
            "👥 <b>Реферальная программа</b>\n\n"
            f"Приглашайте друзей — вы получаете <b>40%</b> от каждого их платежа на партнёрский баланс.\n\n"
            f"🔗 Ваша ссылка:\n<code>{bot_link}</code>\n"
            f"{invited_note}\n"
            f"💰 Баланс: <b>{balance:,.2f} ₽</b>\n\n"
            "Балансом можно оплатить подписку или подарок — при покупке он применится автоматически."
        ),
        reply_markup=main_menu_keyboard(await get_settings()),
    )


# ========= ПОКУПКА ПОДПИСКИ: визард как на сайте (тариф → устройства → промокод → оплата) =========

class BuyFSM(StatesGroup):
    promo = State()          # ждём текст промокода
    payment = State()        # выбранный способ оплаты запоминаем в data


def _kb_back_to_plans():
    kb = InlineKeyboardBuilder()
    kb.button(text="← К тарифам", callback_data="menu:subscribe")
    kb.adjust(1)
    return kb.as_markup()


async def _kb_plans():
    kb = InlineKeyboardBuilder()
    plans = await get_plans()
    for p in plans:
        kb.button(text=f"{p.name} — {p.price:,.0f} ₽", callback_data=f"buy:plan:{p.key}")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "menu:subscribe")
async def subscribe_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    s = await get_settings()
    await callback.message.answer(
        text=(
            "💎 <b>Оформление подписки</b>\n\n"
            "Покупка доступна от одного месяца. Трафик — <b>безлимитный</b>.\n\n"
            "<b>Шаг 1 из 3</b> — выберите тариф:"
        ),
        reply_markup=await _kb_plans(),
    )


@router.callback_query(F.data.startswith("buy:plan:"))
async def buy_choose_devices(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":")[2]
    plan = await get_plan(key)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.update_data(plan_key=key)
    s = await get_settings()
    kb = InlineKeyboardBuilder()
    n = 3
    kb.button(text="➖", callback_data=f"buy:dev:{key}:{n - 1}")
    kb.button(text=f"{n} устройства", callback_data="noop")
    kb.button(text="➕", callback_data=f"buy:dev:{key}:{n + 1}")
    kb.button(text="Дальше: промокод →", callback_data=f"buy:promo:{key}:{n}")
    kb.button(text="↩️ Другой тариф", callback_data="menu:subscribe")
    kb.adjust(3, 1, 1)

    extra = s.device_price_month
    await callback.message.edit_text(
        text=(
            f"💎 Тариф <b>{plan.name}</b> — {plan.price:,.0f} ₽\n\n"
            f"<b>Шаг 2 из 3</b> — количество устройств.\n"
            f"В базе — 3 устройства, каждое дополнительное +{extra:,.0f} ₽/мес."
        ),
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("buy:dev:"))
async def buy_devices_step(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, key, n = callback.data.split(":")
    n = max(3, min(10, int(n)))
    s = await get_settings()
    kb = InlineKeyboardBuilder()
    kb.button(text="➖", callback_data=f"buy:dev:{key}:{n - 1}")
    kb.button(text=f"{n} {'устройство' if n == 3 else 'устройства' if n <= 4 else 'устройств'}", callback_data="noop")
    kb.button(text="➕", callback_data=f"buy:dev:{key}:{n + 1}")
    kb.button(text="Дальше: промокод →", callback_data=f"buy:promo:{key}:{n}")
    kb.button(text="↩️ Другой тариф", callback_data="menu:subscribe")
    kb.adjust(3, 1, 1)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    except Exception:
        pass  # нажали слишком быстро, разметка уже такая


@router.callback_query(F.data.startswith("buy:promo:"))
async def buy_promo_step(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, key, n = callback.data.split(":")
    plan = await get_plan(key)
    await state.set_state(BuyFSM.promo)
    await state.update_data(plan_key=key, devices=int(n))
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить →", callback_data=f"buy:method:{key}:{n}")
    kb.button(text="↩️ К устройствам", callback_data=f"buy:plan:{key}")
    kb.adjust(1)
    await callback.message.edit_text(
        text=(
            f"🎁 <b>Шаг 3 из 3</b> — промокод.\n\n"
            f"Тариф «{plan.name}» на {n} устройств выбран. "
            f"Если у вас есть промокод — отправьте его следующим сообщением. "
            f"Нет — нажмите «Пропустить»."
        ),
        reply_markup=kb.as_markup(),
    )


@router.message(BuyFSM.promo, F.text)
async def buy_promo_input(message: Message, state: FSMContext):
    promo = message.text.strip()
    data = await state.get_data()
    key, n = data["plan_key"], data["devices"]
    plan = await get_plan(key)

    # Бэкенда промокодов пока нет — фиксируем код, применить сможет оператор при оплате
    await state.update_data(promo=promo)
    await _show_payment_step(message, state, plan, n, promo)


@router.callback_query(F.data.startswith("buy:method:"))
async def buy_promo_skipped(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, key, n = callback.data.split(":")
    plan = await get_plan(key)
    await _show_payment_step(callback.message, state, plan, int(n), None)


async def _show_payment_step(msg_for: Message, state: FSMContext, plan, n: int, promo: str | None):
    await state.set_state(BuyFSM.payment)
    await state.update_data(payment=None)
    kb = InlineKeyboardBuilder()
    for pid, label in PAYMENT_METHODS:
        kb.button(text=label, callback_data=f"buy:pay:{pid}")
    kb.button(text="✅ К оплате", callback_data="buy:checkout")
    kb.adjust(1)
    promo_line = f"\n🎟 Промокод: <code>{promo}</code>" if promo else ""
    await msg_for.answer(
        text=(
            f"💳 <b>Способ оплаты</b>\n\n"
            f"Тариф «{plan.name}» · {n} устройств{promo_line}\n\n"
            f"Выберите способ — и проверьте итог перед оплатой."
        ),
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("buy:pay:"))
async def buy_payment_pick(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    pid = callback.data.split(":")[2]
    label = dict(PAYMENT_METHODS).get(pid, pid)
    await state.update_data(payment=pid)
    await callback.message.answer(
        text=f"Выбрано: <b>{label}</b>. Нажмите «✅ К оплате», когда будете готовы.",
    )


@router.callback_query(F.data == "buy:checkout")
async def buy_checkout(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    plan = await get_plan(data.get("plan_key", ""))
    n = data.get("devices", 3)
    promo = data.get("promo")
    pid = data.get("payment")
    pay_label = dict(PAYMENT_METHODS).get(pid, "не выбран")

    # реферальный баланс — покажем, сколько доступно списать
    u = await get_user(callback.from_user.id)
    bonus = float(u.referral_balance or 0) if u else 0.0
    bonus_line = (
        f"\n💰 Реферальный бонус: <b>{bonus:,.2f} ₽</b> — будет списан при оплате"
        if bonus > 0 else ""
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Подписка", callback_data="menu:subscribe")
    kb.button(text="👥 Реф. программа", callback_data="menu:referral")
    kb.adjust(1)
    await state.clear()
    await callback.message.answer(
        text=(
            "🧾 <b>Итог заказа</b>\n\n"
            f"Тариф: <b>{plan.name if plan else '—'}</b>\n"
            f"Устройств: <b>{n}</b>\n"
            f"Способ оплаты: <b>{pay_label}</b>"
            + (f"\nПромокод: <code>{promo}</code>" if promo else "")
            + bonus_line + "\n\n"
            "🚧 <b>Приём платежей скоро заработает</b> — подключаем Platega и CISPay. "
            "Как только оплатим — ключ придёт сюда и в кабинет на сайте."
        ),
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "noop")
async def buy_noop(callback: CallbackQuery):
    await callback.answer()


# ========= ПРОМОКОД =========

@router.callback_query(F.data == "menu:promo")
async def promo_menu(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 К тарифам", callback_data="menu:subscribe")
    kb.button(text=f"💬 {TG_SUPPORT_LABEL}", url=TG_SUPPORT_URL)
    kb.adjust(1)
    await callback.message.answer(
        text=(
            "🎁 <b>Промокоды</b>\n\n"
            "Промокоды заработают вместе с приёмом платежей — поле для них уже есть "
            "в оформлении подписки (шаг 3) на сайте и в этом боте.\n\n"
            "Как только запустим — актуальные коды будут появляться в этом разделе "
            "и в нашем канале. Следите за новостями 😉"
        ),
        reply_markup=kb.as_markup(),
    )


# ========= INFO =========

@router.callback_query(F.data == "menu:info")
async def info_menu(callback: CallbackQuery):
    await callback.answer()
    s = await get_settings()
    kb = InlineKeyboardBuilder()
    web = settings.web_api_url if settings.web_api_url.startswith("http") else None
    if web and "localhost" not in web:
        kb.button(text="🌐 Личный кабинет на сайте", url=web)
    kb.button(text=f"💬 {TG_SUPPORT_LABEL}", url=TG_SUPPORT_URL)
    kb.adjust(1)
    await callback.message.answer(
        text=(
            "ℹ️ <b>INFO</b>\n\n"
            f"{s.bot_name} — {s.bot_tagline}.\n\n"
            "⚡️ <b>Что важно знать:</b>\n"
            "• Трафик — <b>безлимитный</b> на всех тарифах\n"
            "• Подписка — от одного месяца, чем дольше срок, тем выгоднее\n"
            "• Пробный период — 3 дня бесплатно, без привязки карты\n"
            "• До 10 устройств в одной подписке\n"
            "• Партнёрская программа — 40% с платежей приглашённых\n\n"
            "🔐 <b>Кабинет на сайте</b> — управление устройствами, рефералы, история и подарки. "
            "Войти можно через этого бота в один клик.\n\n"
            f"💬 Вопросы — в поддержку: {TG_SUPPORT_URL}"
        ),
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("auth:"))
async def handle_auth_callback(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Неверные данные", show_alert=True)
        return

    action = parts[1]
    code = parts[2]
    approved = (action == "yes")
    user_id = callback.from_user.id
    username = callback.from_user.username

    api_url = f"{settings.web_api_url}/api/auth/telegram/confirm"
    headers = {
        "x-bot-secret": settings.telegram_auth_secret,
        "Content-Type": "application/json",
    }
    payload = {
        "code": code,
        "approved": approved,
        "tgId": user_id,
        "tgUsername": username,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    if approved:
                        await callback.message.edit_text(
                            text="✅ <b>Авторизация подтверждена!</b>\n\nМожете вернуться на сайт — вас автоматически перенаправит в личный кабинет."
                        )
                    else:
                        await callback.message.edit_text(
                            text="❌ <b>Авторизация отклонена.</b>"
                        )
                else:
                    err = data.get("error", "Unknown error")
                    await callback.message.edit_text(
                        text=f"⚠️ Ошибка авторизации: {err}"
                    )
    except Exception as e:
        await callback.message.edit_text(
            text=f"⚠️ Не удалось связаться с сайтом: {e}"
        )
    await callback.answer()


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await send_main_menu(message)


@router.callback_query(F.data == "menu:back")
async def back_to_menu(callback: CallbackQuery):
    await callback.answer()
    await send_main_menu(callback.message)


@router.callback_query(F.data.startswith("menu:"))
async def handle_menu(callback: CallbackQuery):
    section = callback.data.split(":")[1]
    await callback.answer()
    s = await get_settings()
    await callback.message.answer(
        text=get_stub_text(SECTION_NAMES.get(section, section)),
        reply_markup=main_menu_keyboard(s),
    )
