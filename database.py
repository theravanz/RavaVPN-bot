from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Numeric, String, Text,
    func, inspect as sa_inspect, select, text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionMaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ========= НАСТРОЙКИ БОТА (строка id=1) =========
class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    bot_name: Mapped[str] = mapped_column(String(64), default="RavaVPN")
    bot_tagline: Mapped[str] = mapped_column(String(256), default="Защитник ваших интернет-прав")
    locations: Mapped[str] = mapped_column(
        Text, default="⚡ Быстрый | АВТО\n🌍 Остальные | АВТО\n📱 Мобильный | АВТО"
    )
    main_menu_photo_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # контакты
    channel_url: Mapped[str] = mapped_column(String(256), default="")     # канал новостей
    support_url: Mapped[str] = mapped_column(String(256), default="")     # техподдержка
    logs_channel: Mapped[str] = mapped_column(String(64), default="")     # канал уведомлений
    timezone_offset: Mapped[str] = mapped_column(String(16), default="UTC+03:00")

    # пробная подписка
    trial_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_days: Mapped[int] = mapped_column(default=3)

    # тумблеры фич
    roulette_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gifts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    marketing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # доп. устройство
    device_price_month: Mapped[float] = mapped_column(Numeric(10, 2), default=25)  # при покупке/продлении
    device_price_day: Mapped[float] = mapped_column(Numeric(10, 2), default=1)     # добавление к активной

    # режим работы: autonomous | online | hybrid
    bot_mode: Mapped[str] = mapped_column(String(16), default="hybrid")


# ========= ТАРИФЫ (теперь в БД — цены правятся из админки) =========
class Plan(Base):
    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(8), primary_key=True)   # "1d", "1m", "12m"...
    name: Mapped[str] = mapped_column(String(32))
    days: Mapped[int]
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(default=0)


# минимальные цены — константы, админ не может поставить ниже
MIN_PRICES = {"1m": 250, "3m": 650, "6m": 1150, "12m": 2000}

DEFAULT_PLANS = [
    ("1d",  "1 день",    1,   49.0,   0, False),  # приз рулетки, не продаётся
    ("3d",  "3 дня",     3,   99.0,   1, False),  # триал новичкам, не продаётся
    ("1m",  "1 месяц",   30,  250.0,  2, True),
    ("3m",  "3 месяца",  90,  650.0,  3, True),
    ("6m",  "6 месяцев", 180, 1250.0, 4, True),
    ("12m", "1 год",     365, 2500.0, 5, True),
]


# ========= ПОЛЬЗОВАТЕЛИ =========
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # tg_id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    last_active: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # рефералы: кто пригласил (tg_id пригласившего) и локальное зеркало
    # партнёрского баланса (источник истины — сайт, кэш — для чата и
    # покупок в автономном режиме)
    invited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    referral_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    referral_balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    referral_spent: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


# ========= OUTBOX: события для сайта при недоступности =========
class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # eventId
    kind: Mapped[str] = mapped_column(String(32))  # referral_attach | referral_reward | referral_spend
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


# ========= ПОДПИСКИ / ПЛАТЕЖИ =========
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(8))  # -> Plan.key
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    kind: Mapped[str] = mapped_column(String(16))  # "subscription" | "topup"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ========= МИГРАЦИИ (create_all не умеет добавлять колонки) =========
def _add_missing_columns(sync_conn):
    insp = sa_inspect(sync_conn)

    ucols = {c["name"] for c in insp.get_columns("users")}
    if "is_banned" not in ucols:
        sync_conn.execute(text(
            "ALTER TABLE users ADD COLUMN is_banned Boolean NOT NULL DEFAULT False"
        ))
    users_new = {
        "invited_by": "BIGINT NULL",
        "referral_code": "VARCHAR(32) NULL",
        "referral_balance": "Numeric(10,2) NOT NULL DEFAULT 0",
        "referral_spent": "Numeric(10,2) NOT NULL DEFAULT 0",
    }
    for name, ddl in users_new.items():
        if name not in ucols:
            sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))

    scols = {c["name"] for c in insp.get_columns("settings")}
    settings_new = {
        "balance": "Numeric(10,2) NOT NULL DEFAULT 0",
        "channel_url": "VARCHAR(256) NOT NULL DEFAULT ''",
        "support_url": "VARCHAR(256) NOT NULL DEFAULT ''",
        "logs_channel": "VARCHAR(64) NOT NULL DEFAULT ''",
        "timezone_offset": "VARCHAR(16) NOT NULL DEFAULT 'UTC+03:00'",
        "trial_enabled": "Boolean NOT NULL DEFAULT True",
        "trial_days": "INTEGER NOT NULL DEFAULT 3",
        "roulette_enabled": "Boolean NOT NULL DEFAULT True",
        "gifts_enabled": "Boolean NOT NULL DEFAULT False",
        "marketing_enabled": "Boolean NOT NULL DEFAULT False",
        "device_price_month": "Numeric(10,2) NOT NULL DEFAULT 25",
        "device_price_day": "Numeric(10,2) NOT NULL DEFAULT 1",
    }
    for name, ddl in settings_new.items():
        if name not in scols:
            sync_conn.execute(text(f"ALTER TABLE settings ADD COLUMN {name} {ddl}"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    async with SessionMaker() as session:
        if not await session.get(Settings, 1):
            session.add(Settings(id=1))
        # сидируем тарифы при первом запуске
        for key, name, days, price, sort, active in DEFAULT_PLANS:
            p = await session.get(Plan, key)
            if not p:
                session.add(Plan(key=key, name=name, days=days, price=price, sort=sort, is_active=active))
            else:
                p.is_active = active  # пока нет UI для активности — держим в синхроне
        await session.commit()


async def get_settings() -> Settings:
    async with SessionMaker() as session:
        return await session.get(Settings, 1)


async def get_plans() -> list[Plan]:
    async with SessionMaker() as session:
        return (await session.scalars(
            select(Plan).where(Plan.is_active == True).order_by(Plan.sort)  # noqa: E712
        )).all()


async def get_plan(key: str) -> Plan | None:
    async with SessionMaker() as session:
        return await session.get(Plan, key)


async def upsert_user(tg_id: int, username: str | None):
    async with SessionMaker() as session:
        user = await session.get(User, tg_id)
        if not user:
            session.add(User(id=tg_id, username=username))
        else:
            user.username = username
        await session.commit()


async def is_user_banned(tg_id: int) -> bool:
    async with SessionMaker() as session:
        u = await session.get(User, tg_id)
        return bool(u and u.is_banned)


# ========= РЕФЕРАЛЫ =========
REF_CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _gen_ref_code() -> str:
    import random
    return "ref_" + "".join(random.choice(REF_CODE_ALPHABET) for _ in range(8))


async def ensure_referral_code(tg_id: int, username: str | None) -> str:
    """Гарантирует реферальный код пользователя (локально, лениво)."""
    async with SessionMaker() as session:
        u = await session.get(User, tg_id)
        if not u:
            u = User(id=tg_id, username=username)
            session.add(u)
        if not u.referral_code:
            u.referral_code = _gen_ref_code()
        await session.commit()
        return u.referral_code


async def get_user(tg_id: int) -> User | None:
    async with SessionMaker() as session:
        return await session.get(User, tg_id)


async def find_user_by_ref_code(code: str) -> User | None:
    async with SessionMaker() as session:
        return (await session.scalars(select(User).where(User.referral_code == code))).first()


async def attach_referral(invited_tg_id: int, username: str | None, inviter_tg_id: int) -> bool:
    """Локальная привязка «кто кого пригласил». True — привязали впервые."""
    if invited_tg_id == inviter_tg_id:
        return False
    async with SessionMaker() as session:
        u = await session.get(User, invited_tg_id)
        if not u:
            u = User(id=invited_tg_id, username=username)
            session.add(u)
        if u.invited_by:
            return False  # уже приглашён кем-то — не перезаписываем
        u.invited_by = inviter_tg_id
        await session.commit()
        return True


async def mirror_reward(inviter_tg_id: int, amount: float):
    """Локальное зеркало начисления 40% (источник истины — сайт)."""
    async with SessionMaker() as session:
        u = await session.get(User, inviter_tg_id)
        if u:
            u.referral_balance = (u.referral_balance or 0) + amount
            await session.commit()


async def mirror_spend(tg_id: int, amount: float) -> bool:
    """Локальное списание баланса на покупку. False — не хватает."""
    async with SessionMaker() as session:
        u = await session.get(User, tg_id)
        if not u or (u.referral_balance or 0) < amount:
            return False
        u.referral_balance = (u.referral_balance or 0) - amount
        u.referral_spent = (u.referral_spent or 0) + amount
        await session.commit()
        return True


async def report_referral_reward(payer_tg_id: int, amount_rub: float, payment_note: str = "") -> float:
    """
    Вызывается из места, где фиксируется реальный платёж приглашённого.
    Начисляет пригласившему 40% локально (мгновенно видит в чате) и
    кладёт событие на сайт через outbox. Возвращает сумму начисления.
    """
    payer = await get_user(payer_tg_id)
    if not payer or not payer.invited_by:
        return 0.0

    inviter_id = payer.invited_by
    reward = round(amount_rub * 0.4, 2)
    await mirror_reward(inviter_id, reward)

    from web_client import web_post
    await web_post(
        "referral_reward",
        "/api/referrals/event",
        {
            "tgId": str(payer_tg_id),
            "fromTgId": str(inviter_id),
            "amount": int(round(amount_rub * 100)),  # сайт считает 40% сам
            "note": payment_note,
        },
    )
    return reward


# ========= СВОДКА ДЛЯ ШАПКИ АДМИНКИ =========
async def get_admin_summary() -> dict:
    now = _utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionMaker() as session:
        total = await session.scalar(select(func.count()).select_from(User)) or 0
        new_today = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= start_of_day)
        ) or 0
        subs_active = await session.scalar(
            select(func.count()).select_from(Subscription).where(Subscription.expires_at > now)
        ) or 0
        turnover = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0))
        ) or 0
        sales_today = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.kind == "subscription", Payment.created_at >= start_of_day
            )
        ) or 0

    return {
        "total": total, "new_today": new_today, "subs_active": subs_active,
        "turnover": float(turnover), "sales_today": float(sales_today),
    }


# ========= ПОЛНАЯ СТАТИСТИКА =========
async def get_stats() -> dict:
    now = _utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with SessionMaker() as session:
        total = await session.scalar(select(func.count()).select_from(User)) or 0
        new_day = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= start_of_day)
        ) or 0
        new_week = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= week_ago)
        ) or 0
        new_month = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= month_ago)
        ) or 0

        plans = (await session.scalars(select(Plan).order_by(Plan.sort))).all()
        subs = (await session.scalars(
            select(Subscription).where(Subscription.expires_at > now)
        )).all()

        buys_count = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.kind == "subscription")
        ) or 0
        buys_sum = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.kind == "subscription")
        ) or 0
        topups_count = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.kind == "topup")
        ) or 0
        topups_sum = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.kind == "topup")
        ) or 0

    return {
        "total": total, "new_day": new_day, "new_week": new_week, "new_month": new_month,
        "plans": plans,
        "subs_total": len(subs),
        "subs_paid": sum(1 for s in subs if s.expires_at - now >= timedelta(days=3)),
        "plan_counts": Counter(s.plan for s in subs),
        "buys_count": buys_count, "buys_sum": float(buys_sum),
        "topups_count": topups_count, "topups_sum": float(topups_sum),
    }


def fmt_money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")