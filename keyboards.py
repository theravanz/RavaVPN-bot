from aiogram.utils.keyboard import InlineKeyboardBuilder


# ========= ЮЗЕРСКОЕ МЕНЮ =========
def main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Подписка", callback_data="menu:subscribe")
    kb.button(text="👥 Реф. программа", callback_data="menu:referral")
    kb.button(text="🎲 Рулетка", callback_data="menu:roulette")
    kb.button(text="🎁 Промокод", callback_data="menu:promo")
    kb.button(text="ℹ️ INFO", callback_data="menu:info")
    kb.button(text="🚀 Хочу свой VPN", callback_data="menu:own_vpn")
    kb.button(text="🌐 Change language (EN)", callback_data="menu:lang")
    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()


# ========= АДМИНКА =========
def admin_main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="🎨 Кастомизация", callback_data="adm:custom")
    kb.button(text="📢 Рассылка", callback_data="adm:broadcast")
    kb.button(text="👥 Пользователи", callback_data="adm:users")
    kb.button(text="🚪 Выйти в меню", callback_data="adm:exit")
    kb.adjust(1)
    return kb.as_markup()


def custom_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название бота", callback_data="cus:name")
    kb.button(text="💬 Тэглайн", callback_data="cus:tagline")
    kb.button(text="🌎 Локации", callback_data="cus:locations")
    kb.button(text="🖼 Фото меню", callback_data="cus:photo")
    kb.button(text="🗑 Убрать фото", callback_data="cus:photo_del")
    kb.button(text="⬅️ Назад", callback_data="adm:main")
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить", callback_data="bc:send")
    kb.button(text="❌ Отмена", callback_data="bc:cancel")
    kb.adjust(2)
    return kb.as_markup()

