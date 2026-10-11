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


# ========= ТАРИФЫ (теперь в БД — цены правятся из админки) =========
class Plan(Base):
    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(8), primary_key=True)   # "1d", "1m", "12m"...
    name: Mapped[str] = mapped_column(String(32))
    days: Mapped[int]
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(default=0)


DEFAULT_PLANS = [
    ("1d",  "1 день",    1,   49.0,  0),
    ("3d",  "3 дня",     3,   99.0,  1),
    ("1m",  "1 месяц",   30,  250.0, 2),
    ("3m",  "3 месяца",  90,  650.0, 3),
    ("6m",  "6 месяцев", 180, 1250.0, 4),
    ("12m", "1 год",     365, 2500.0, 5),
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
        for key, name, days, price, sort in DEFAULT_PLANS:
            if not await session.get(Plan, key):
                session.add(Plan(key=key, name=name, days=days, price=price, sort=sort))
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