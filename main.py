import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from config import ADMIN_BOT_TOKEN, BOT_TOKEN, ENVIRONMENT, PORT
from handlers import admin, cart, catalog, courier, errors, start
from middlewares import setup_throttling

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Diqqat: bu import ataylab logging sozlamasidan KEYIN turadi — database.db
# import paytida Firestore holati haqida log yozadi va u formatlangan
# ko'rinishda chiqishi kerak.
from database.db import CATEGORIES, SAMPLE_PRODUCTS  # noqa: E402


async def safe_start_polling(bot: Bot, dp: Dispatcher, bot_name: str):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info(f"🚀 {bot_name} polling started...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"⚠️ {bot_name} ishga tushmadi: {e}. (Telegram tokenini tekshiring)")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass

async def main():
    # 1. HTTP Server for Health Check & Local Testing
    async def handle_health_check(request):
        return web.Response(text="ASOX Market Telegram Bot Service is running locally OK! (Port: 8080)\n")

    async def handle_categories(request):
        return web.json_response({"ok": True, "categories": CATEGORIES})

    async def handle_products(request):
        return web.json_response({"ok": True, "products": SAMPLE_PRODUCTS})

    async def handle_simulate_start(request):
        return web.json_response({
            "ok": True,
            "bot": "@AsoxUz_Bot",
            "message": "👋 Assalomu alaykum! ASOX Market katalog botiga xush kelibsiz!",
            "buttons": ["🛍 Mahsulotlar Katalogi", "🔍 Qidiruv", "🌐 ASOX Market sayti", "👤 Profilim"]
        })

    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    app.router.add_get('/api/categories', handle_categories)
    app.router.add_get('/api/products', handle_products)
    app.router.add_get('/api/test/start', handle_simulate_start)

    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        await site.start()
        logging.info(f"🚀 Local Bot Server & Health Check running on http://localhost:{PORT}...")
    except Exception as e:
        logging.warning(f"⚠️ Health check HTTP server could not start on port {PORT}: {e}")

    polling_tasks = []

    # 2. Main User & Seller Bot (@AsoxUz_Bot)
    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        bot_user = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp_user = Dispatcher()
        dp_user.include_router(start.router)
        dp_user.include_router(catalog.router)
        dp_user.include_router(cart.router)
        dp_user.include_router(courier.router)
        errors.setup_errors(dp_user)
        setup_throttling(dp_user)
        polling_tasks.append(safe_start_polling(bot_user, dp_user, "ASOX Market User Bot (@AsoxUz_Bot)"))

    # 3. Dedicated Admin Bot (@asox_admin_bot)
    if ADMIN_BOT_TOKEN and ADMIN_BOT_TOKEN != "YOUR_ADMIN_BOT_TOKEN_HERE":
        bot_admin = Bot(token=ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp_admin = Dispatcher()
        dp_admin.include_router(admin.router)
        errors.setup_errors(dp_admin)
        setup_throttling(dp_admin, rate_limit=0.3)
        polling_tasks.append(safe_start_polling(bot_admin, dp_admin, "ASOX Market Admin Bot (@asox_admin_bot)"))

    if not polling_tasks:
        logging.error(
            "❌ Hech qanday bot tokeni sozlanmagan. BOT_TOKEN (va kerak bo'lsa "
            "ADMIN_BOT_TOKEN) ni .env yoki muhit o'zgaruvchilariga qo'shing."
        )
        return 1

    logging.info("Muhit: %s | Ishga tushgan botlar: %s", ENVIRONMENT, len(polling_tasks))
    results = await asyncio.gather(*polling_tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logging.error("Polling vazifasi xato bilan tugadi: %s", result)

    # Barcha polling vazifalari tugadi — bu odatda token yaroqsizligini bildiradi.
    logging.error("⚠️ Barcha botlar to'xtadi. Tokenlarni va tarmoqni tekshiring.")
    return 1

if __name__ == "__main__":
    # Diqqat: sys.exit() ni try ichida chaqirmaymiz — u ko'targan SystemExit
    # o'sha except tomonidan yutilib, jarayon 0 kod bilan tugab qolardi.
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bots stopped.")
        exit_code = 0
    sys.exit(exit_code or 0)
