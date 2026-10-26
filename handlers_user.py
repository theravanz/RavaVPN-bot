import aiohttp
from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import get_settings, upsert_user
from keyboards import main_menu_keyboard
from texts import get_main_menu_text, get_stub_text

router = Router()

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
    if args:
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
    await send_main_menu(message)


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
