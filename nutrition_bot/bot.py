"""Main bot entry point."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from nutrition_bot.config import BOT_TOKEN
from nutrition_bot.database.models import init_db
from nutrition_bot.handlers import start, schedule, meals, ai_chat, settings, log_food

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("NUTRITION_BOT_TOKEN is not set!")
        sys.exit(1)

    logger.info("Initializing database...")
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Register routers — order matters for callback handling
    dp.include_router(start.router)
    dp.include_router(schedule.router)
    dp.include_router(log_food.router)
    dp.include_router(meals.router)
    dp.include_router(ai_chat.router)
    dp.include_router(settings.router)

    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
