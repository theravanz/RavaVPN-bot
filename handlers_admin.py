import asyncio

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database import SessionMaker, User, get_settings, get_stats
from filters import IsAdmin
from keyboards import admin_main_kb, confirm_kb, custom_menu_kb, main_menu_keyboard

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
    s = await get_settings()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("👾 Главное меню", reply_markup=main_menu_keyboard())


# ========= СТАТИСТИКА =========
@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery):
    st = await get_stats()
    await callback.answer()
    await callback.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей всего: <b>{st['total']}</b>\n"
        f"🆕 За сегодня: <b>{st['today']}</b>\n"
        f"📈 За 7 дней: <b>{st['week']}</b>",
        reply_markup=admin_main_kb(),
    )


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
        s = await session.get(_Settings, 1)
        s.main_menu_photo_id = None
        await session.commit()
    await callback.answer("🗑 Фото убрано", show_alert=True)


@router.message(CustomStates.name, F.text)
async def save_name(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(_Settings, 1)
        s.bot_name = message.text.strip()[:64]
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Название сохранено: <b>{s.bot_name}</b>")


@router.message(CustomStates.tagline, F.text)
async def save_tagline(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(_Settings, 1)
        s.bot_tagline = message.text.strip()[:256]
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Тэглайн сохранён: <i>{s.bot_tagline}</i>")


@router.message(CustomStates.locations, F.text)
async def save_locations(message: Message, state: FSMContext):
    async with SessionMaker() as session:
        s = await session.get(_Settings, 1)
        s.locations = message.text.strip()
        await session.commit()
    await state.clear()
    count = len(s.locations.splitlines())
    await message.answer(f"✅ Локации сохранены ({count} шт.)")


@router.message(CustomStates.photo, F.photo)
async def save_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    async with SessionMaker() as session:
        s = await session.get(_Settings, 1)
        s.main_menu_photo_id = file_id
        await session.commit()
    await state.clear()
    await message.answer("✅ Фото сохранено, проверь /start")


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
    await state.set_state(BroadcastStates.confirm) if False else None
    # сохраняем и ждём подтверждения
    await state.set_data({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.set_state(BroadcastStates.confirm)
    await message.answer(
        f"👀 <b>Превью выше</b> ☝️\n\nПолучателей: <b>{await _users_count()}</b>\nОтправляем?",
        reply_markup=confirm_kb(),
    )


@router.message(BroadcastStates.confirm)  # на случай если юзер написал ещё раз
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
        result = await session.scalars(select(User.id))
        user_ids = result.all()

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
        await asyncio.sleep(0.05)  # ~20 msg/sec, чтобы не словить флуд от телеги

    await status.edit_text(
        f"📢 <b>Рассылка завершена</b>\n\n✅ Доставлено: <b>{ok}</b>\n❌ Не доставлено: <b>{failed}</b>",
        reply_markup=admin_main_kb(),
    )


async def _users_count() -> int:
    from sqlalchemy import func
    async with SessionMaker() as session:
        return await session.scalar(select(func.count()).select_from(User)) or 0


# ========= /cancel — универсальная отмена FSM =========
@router.message(F.text == "/cancel")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Отменено.", reply_markup=admin_main_kb());

