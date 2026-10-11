from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionMaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ========= НАСТРОЙКИ БОТА (всегда одна строка с id=1) =========
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


# ========= ПОЛЬЗОВАТЕЛИ =========
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # tg_id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_active: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


async def init_db():
    """Создаёт таблицы и строку настроек при первом запуске."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionMaker() as session:
        if not await session.get(Settings, 1):
            session.add(Settings(id=1))
            await session.commit()


async def get_settings() -> Settings:
    """Читает настройки (на объёмах одного бота — норм, кэш добавим позже)."""
    async with SessionMaker() as session:
        return await session.get(Settings, 1)


async def upsert_user(tg_id: int, username: str | None):
    """Регистрирует юзера или обновляет last_active."""
    async with SessionMaker() as session:
        user = await session.get(User, tg_id)
        if not user:
            session.add(User(id=tg_id, username=username))
        else:
            user.username = username
        await session.commit()


async def get_stats() -> dict:
    async with SessionMaker() as session:
        total = await session.scalar(select(func.count()).select_from(User))
        today = await session.scalar(
            select(func.count()).select_from(User).where(
                User.created_at >= func.date_trunc("day", func.now())
            )
        ) if engine.url.drivername.startswith("postgres") else await session.scalar(
            select(func.count()).select_from(User).where(
                func.date(User.created_at) == func.date("now")
            )
        )
        week = await session.scalar(
            select(func.count()).select_from(User).where(
                User.created_at >= func.datetime("now", "-7 days")
            )
        ) if engine.url.drivername.startswith("sqlite") else None
    return {"total": total or 0, "today": today or 0, "week": week or 0}
