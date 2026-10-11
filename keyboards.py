from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Settings


# ========= ЮЗЕРСКОЕ МЕНЮ (учитывает тумблеры) =========
def main_menu_keyboard(s: Settings):
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Подписка", callback_data="menu:subscribe")
    kb.button(text="👥 Реф. программа", callback_data="menu:referral")
    if s.roulette_enabled:
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
    kb.button(text="⚙️ Кастомизация (конфиг)", callback_data="adm:custom")
    kb.button(text="👥 Работа с пользователями и подписками", callback_data="adm:users")
    kb.button(text="🏦 Вывод средств", callback_data="adm:withdraw")
    kb.button(text="💰 Пополнить баланс", callback_data="adm:topup")
    kb.button(text="🎲 Доп. функции", callback_data="adm:extra")
    kb.button(text="📨 Рассылка", callback_data="adm:broadcast")
    kb.button(text="📈 UTM Метки", callback_data="adm:utm")
    kb.button(text="➡️ В меню бота", callback_data="adm:exit")
    kb.adjust(1)
    return kb.as_markup()


def back_kb(target: str = "adm:main"):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=target)
    return kb.as_markup()


def custom_menu_kb(s: Settings):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить название", callback_data="cus:name")
    kb.button(text="🌎 Локации", callback_data="cus:locations")
    kb.button(text="💰 Изменить цену", callback_data="cus:prices")
    kb.button(text="🧩 Главное меню", callback_data="cus:stub")
    kb.button(text="🔗 Ссылка подписки", callback_data="cus:stub")
    kb.button(
        text=f"📨 Маркетинговые сообщения: {'✅' if s.marketing_enabled else '❌'}",
        callback_data="cus:tgl:marketing_enabled",
    )
    kb.button(text="🎁 Пробная подписка", callback_data="cus:trial")
    kb.button(text="📢 Изменить канал", callback_data="cus:channel")
    kb.button(text="👨‍💻 Изменить техподдержку", callback_data="cus:support")
    kb.button(text="🔕 Канал уведомлений", callback_data="cus:logs")
    kb.button(text="🕓 Часовой пояс", callback_data="cus:tz")
    kb.button(text="🖼 Картинки к сообщениям", callback_data="cus:stub")
    kb.button(text="✨ Кастом-эмодзи: ❌", callback_data="cus:stub")
    kb.button(
        text=f"🎰 Рулетка: {'✅ вкл' if s.roulette_enabled else '❌ выкл'}",
        callback_data="cus:tgl:roulette_enabled",
    )
    kb.button(
        text=f"🎁 Подарки клиентам: {'✅ вкл' if s.gifts_enabled else '❌ выкл'}",
        callback_data="cus:tgl:gifts_enabled",
    )
    kb.button(text="➡️ Назад", callback_data="adm:main")
    kb.adjust(1)
    return kb.as_markup()


async def prices_menu_kb():
    from database import get_plans, fmt_money
    plans = await get_plans()
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(
            text=f"{p.name} — {fmt_money(float(p.price))}₽ ✏️",
            callback_data=f"cus:price:{p.key}",
        )
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить", callback_data="bc:send")
    kb.button(text="❌ Отмена", callback_data="bc:cancel")
    kb.adjust(2)
    return kb.as_markup()


# ========= ПОЛЬЗОВАТЕЛИ =========
def users_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Последние юзеры", callback_data="usr:list")
    kb.button(text="🔍 Найти юзера", callback_data="usr:search")
    kb.button(text="⬅️ Назад", callback_data="adm:main")
    kb.adjust(1)
    return kb.as_markup()


def user_card_kb(uid: int, banned: bool):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎁 Выдать подписку", callback_data=f"usr:grant:{uid}")
    kb.button(text="🚫 Забанить" if not banned else "✅ Разбанить", callback_data=f"usr:ban:{uid}")
    kb.button(text="⬅️ К списку", callback_data="usr:menu")
    kb.adjust(1)
    return kb.as_markup()


async def plan_kb():
    from database import get_plans
    plans = await get_plans()
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(text=f"⏰ {p.name}", callback_data=f"usr:plan:{p.key}")
    kb.button(text="❌ Отмена", callback_data="usr:menu")
    kb.adjust(1)
    return kb.as_markup()