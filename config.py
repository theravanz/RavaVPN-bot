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
    web_app_url: str = ""  # Публичный HTTPS URL для кнопок в Telegram (задать в .env)
    telegram_auth_secret: str = ""
    bot_username: str = "RavaVPN_bot"  # для реферальных deep-link'ов

    # Platega API credentials
    PLATEGA_API_URL: str = "https://app.platega.io/transaction/process"
    PLATEGA_SANDBOX_URL: str = "https://app.platega.io/transaction/process"
    PLATEGA_MERCHANT_ID: str = ""
    PLATEGA_SECRET_KEY: str = ""
    PLATEGA_USE_SANDBOX: bool = False  # true для разработки без реальных ключей

    # Webhook secret for bot-web communication — ОБЯЗАТЕЛЬНО задать в .env
    WEBHOOK_SECRET: str = ""


settings = Settings()


def get_admin_ids() -> list[int]:
    if not settings.admin_ids:
        return []
    return [int(x.strip()) for x in settings.admin_ids.split(",") if x.strip()]