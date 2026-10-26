from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Settings


# ========= ЮЗЕРСКОЕ МЕНЮ =========
MODE_ASSISTANT = "assistant"  # бот — только помощник сайту: авторизация и ссылки


def main_menu_keyboard(s: Settings):
    kb = InlineKeyboardBuilder()

    if getattr(s, "bot_mode", "hybrid") == MODE_ASSISTANT:
        # режим «помощник сайту»: вся функциональность — на сайте
        kb.button(text="🌐 Открыть личный кабинет", url=_site_url())
        kb.button(text="ℹ️ INFO", callback_data="menu:info")
        kb.adjust(1)
        return kb.as_markup()

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


def _site_url() -> str:
    from config import settings
    url = settings.web_api_url
    if "localhost" in url:
        return "https://ravavpn.vercel.app"
    return url


# ========= АДМИНКА =========
def admin_main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="🧭 Режим работы бота", callback_data="adm:mode")
    kb.button(text="⚙️ Кастомизация (конфиг)", callback_data="adm:custom")
    kb.button(text="👥 Работа с пользователями и подписками", callback_data="adm:users")
    kb.button(text="🏦 Вывод средств", callback_data="adm:withdraw")
    kb.button(text="💰 Пополнить баланс", callback_data="adm:topup")
    kb.button(text="🎲 Доп. функции", callback_data="adm:extra")
    kb.button(text="📨 Рассылка", callback_data="adm:broadcast")
    kb.button(text="📈 UTM Метки", callback_data="adm:utm")
    kb.button(text="🌐 Веб-панель сайта", callback_data="adm:webpanel")
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
        text=f"📨 Маркетинговые сообщения: {'Вкл.' if s.marketing_enabled else 'Выкл.'}",
        callback_data="cus:marketing",
    )
    kb.button(text="🎁 Пробная подписка", callback_data="cus:trial")
    kb.button(text="📢 Изменить канал", callback_data="cus:channel")
    kb.button(
        text=f"📢 Обязательная подписка: ❌ выкл",
        callback_data="cus:stub",
    )
    kb.button(text="👨‍💻 Изменить техподдержку", callback_data="cus:support")
    kb.button(text="🔔 Канал уведомлений", callback_data="cus:logs")
    kb.button(text="🕓 Часовой пояс", callback_data="cus:tz")
    kb.button(text="🖼 Картинки к сообщениям", callback_data="cus:stub")
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


def prices_menu_kb(plans):
    """plans — только продаваемые (is_active=True)."""
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(text=f"💰 {p.name}", callback_data=f"cus:price:{p.key}")
    kb.button(text="📱 Устройство при покупке/продлении", callback_data="cus:price:dev_month")
    kb.button(text="➕ Устройство при добавлении к активной", callback_data="cus:price:dev_day")
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(1)
    return kb.as_markup()


def trial_kb(enabled: bool, days: int):
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🚫 Выключить пробную подписку" if enabled else "✅ Включить пробную подписку",
        callback_data="cus:trial:toggle",
    )
    for d in (1, 2, 3, 5, 7):
        mark = "✅" if (enabled and days == d) else ""
        kb.button(text=f"{mark} {d} день" if d == 1 else f"{mark} {d} дня", callback_data=f"cus:trial:days:{d}")
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()


def marketing_kb(enabled: bool):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Включить" if not enabled else "❌ Выключить", callback_data="cus:marketing:toggle")
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(1)
    return kb.as_markup()


def tz_kb(current: str):
    kb = InlineKeyboardBuilder()
    for h in range(-11, 13):
        label = f"UTC{h:+03d}:00"
        mark = "✅ " if label == current else ""
        kb.button(text=f"{mark}{label}", callback_data=f"cus:tz:{h:+d}")
    kb.button(text="⬅️ Назад", callback_data="cus:back")
    kb.adjust(3, 3, 3, 3, 3, 3, 3, 3, 1)
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


def plan_kb(plans):
    """plans — продаваемые тарифы для ручной выдачи."""
    kb = InlineKeyboardBuilder()
    for p in plans:
        kb.button(text=f"⏰ {p.name}", callback_data=f"usr:plan:{p.key}")
    kb.button(text="❌ Отмена", callback_data="usr:menu")
    kb.adjust(1)
    return kb.as_markup()