from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

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
async def cmd_start(message: Message):
    await upsert_user(message.from_user.id, message.from_user.username)
    await send_main_menu(message)


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
    await callback.message.answer(
        text=get_stub_text(SECTION_NAMES.get(section, section)),
        reply_markup=main_menu_keyboard(),
    )
