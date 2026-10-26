from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    admin_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///./bot.db"
    web_api_url: str = "http://localhost:3000"
    telegram_auth_secret: str = ""


settings = Settings()


def get_admin_ids() -> list[int]:
    if not settings.admin_ids:
        return []
    return [int(x.strip()) for x in settings.admin_ids.split(",") if x.strip()]