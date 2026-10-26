import re

import aiohttp
from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import (
    attach_referral, ensure_referral_code, find_user_by_ref_code,
    get_settings, get_user, upsert_user,
)
from keyboards import main_menu_keyboard
from texts import get_main_menu_text, get_stub_text
from web_client import fetch_web_stats, web_post

router = Router()

AUTH_CODE_RE = re.compile(r"^[0-9a-f]{24}$")

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

    code = await ensure_referral_code(tg_id, callback.from_user.username)
    bot_link = f"https://t.me/{settings.bot_username}?start={code}"
    u = await get_user(tg_id)

    balance = float(u.referral_balance or 0) if u else 0.0
    invited_note = ""

    stats = await fetch_web_stats(tg_id)
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
