import asyncio
import logging
import os
from dotenv import load_dotenv


from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.types import LinkPreviewOptions

from database import init_db
from handlers_admin import router as admin_router
from handlers_user import router as user_router

load_dotenv()   # ← подхватит .env, если он есть; на Render молча пропустит

# ========= ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render подставляет сам: https://xxx.onrender.com
PORT = int(os.getenv("PORT", 8080))                     # Render тоже подставляет сам

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")            # опционально: openssl rand -hex 32

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан — добавь в переменные окружения")

# ========= ИНИЦИАЛИЗАЦИЯ =========
bot = Bot(
    token=BOT_TOKEN, 
    default=DefaultBotProperties(
        link_preview=LinkPreviewOptions(is_disabled=True)
    )
)

dp = Dispatcher()

dp.include_router(admin_router)
dp.include_router(user_router)


# ========= STARTUP (срабатывает в обоих режимах) =========
async def on_startup(bot: Bot):
    logging.info("🗄 Подключаюсь к БД...")
    await init_db()
    logging.info("🗄 БД готова")

    if WEBHOOK_URL:
        await bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=dp.resolve_used_update_types(),
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logging.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        await bot.delete_webhook(drop_pending_updates=False)
        logging.info("✅ Режим polling")

    logging.info(f"🌍 PORT={PORT}, WEBHOOK_URL={WEBHOOK_URL}")


# ========= HEALTHCHECK =========
async def health(_: web.Request) -> web.Response:
    """Сюда пингует Render, и сюда же будешь стучаться cron'ом, чтобы бот не засыпал."""
    return web.Response(text="OK")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    dp.startup.register(on_startup)

    if WEBHOOK_URL:
        # ===== RENDER: вебхук =====
        app = web.Application()

        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=WEBHOOK_SECRET,
        ).register(app, path=WEBHOOK_PATH)

        setup_application(app, dp, bot=bot)
        app.router.add_get("/", health)

        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        # ===== ЛОКАЛЬНО: polling =====
        asyncio.run(dp.start_polling(bot))