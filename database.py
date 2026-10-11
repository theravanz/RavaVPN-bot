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


# ========= ТАРИФЫ =========
PLANS: dict[str, tuple[str, timedelta]] = {
    "1d": ("1 день", timedelta(days=1)),
    "3d": ("3 дня", timedelta(days=3)),
    "1m": ("1 месяц", timedelta(days=30)),
    "3m": ("3 месяца", timedelta(days=90)),
    "6m": ("6 месяцев", timedelta(days=180)),
}


# ========= НАСТРОЙКИ БОТА (строка id=1) =========
class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    bot_name: Mapped[str] = mapped_column(String(64), default="RavaVPN")
    bot_tagline: Mapped[str] = mapped_column(String(256), default="Защитник ваших интернет-прав")
    locations: Mapped[str] = mapped_column(
        Text,
        default="⚡ Быстрый | АВТО\n🌍 Остальные | АВТО\n📱 Мобильный | АВТО",
    )
    main_menu_photo_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


# ========= ПОЛЬЗОВАТЕЛИ =========
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # tg_id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    last_active: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ========= ПОДПИСКИ =========
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(8))  # ключ из PLANS
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


# ========= ПЛАТЕЖИ =========
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    kind: Mapped[str] = mapped_column(String(16))  # "subscription" | "topup"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def _utcnow() -> datetime:
    """UTC без таймзоны — под формат колонок TIMESTAMP."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ========= МИГРАЦИИ =========
def _add_missing_columns(sync_conn):
    insp = sa_inspect(sync_conn)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "balance" not in cols:
        sync_conn.execute(text(
            "ALTER TABLE users ADD COLUMN balance Numeric(10,2) NOT NULL DEFAULT 0"
        ))
    if "is_banned" not in cols:
        sync_conn.execute(text(
            "ALTER TABLE users ADD COLUMN is_banned Boolean NOT NULL DEFAULT False"
        ))

    scols = {c["name"] for c in insp.get_columns("settings")}
    if "balance" not in scols:
        sync_conn.execute(text(
            "ALTER TABLE settings ADD COLUMN balance Numeric(10,2) NOT NULL DEFAULT 0"
        ))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    async with SessionMaker() as session:
        if not await session.get(Settings, 1):
            session.add(Settings(id=1))
            await session.commit()


async def get_settings() -> Settings:
    async with SessionMaker() as session:
        return await session.get(Settings, 1)


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


async def get_active_subscription(user_id: int) -> Subscription | None:
    async with SessionMaker() as session:
        return await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.expires_at > _utcnow())
            .order_by(Subscription.expires_at.desc())
        )


async def get_admin_summary() -> dict:
    """Компактная сводка для шапки админки."""
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
                Payment.kind == "subscription",
                Payment.created_at >= start_of_day,
            )
        ) or 0

    return {
        "total": total,
        "new_today": new_today,
        "subs_active": subs_active,
        "turnover": float(turnover),
        "sales_today": float(sales_today),
    }

# ========= СТАТИСТИКА (как у франшизы) =========
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

        subs = (await session.scalars(
            select(Subscription).where(Subscription.expires_at > now)
        )).all()

        plan_counts = Counter(s.plan for s in subs)

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
        "total": total,
        "new_day": new_day,
        "new_week": new_week,
        "new_month": new_month,
        "subs_total": len(subs),
        "subs_paid": sum(1 for s in subs if s.expires_at - now >= timedelta(days=3)),
        "plan_counts": plan_counts,
        "buys_count": buys_count,
        "buys_sum": float(buys_sum),
        "topups_count": topups_count,
        "topups_sum": float(topups_sum),
    }


def fmt_money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")