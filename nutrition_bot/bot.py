"""Main bot entry point with owner-only access restriction."""

import asyncio
import logging
import sys
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject, Update

from nutrition_bot.config import BOT_TOKEN, OWNER_ID
from nutrition_bot.database.models import init_db
from nutrition_bot.handlers import chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    """Drop all updates from non-owner users."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if OWNER_ID == 0:
            return await handler(event, data)

        user_id = None
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                user_id = event.message.from_user.id
            elif event.callback_query and event.callback_query.from_user:
                user_id = event.callback_query.from_user.id
        else:
            event_user = data.get("event_from_user")
            if event_user:
                user_id = event_user.id

        if user_id is None or user_id != OWNER_ID:
            logger.warning("Blocked access from user_id=%s", user_id)
            return None

        return await handler(event, data)


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

    # Owner-only access
    if OWNER_ID:
        dp.update.outer_middleware(OwnerOnlyMiddleware())
        logger.info("Owner-only mode: user_id=%d", OWNER_ID)
    else:
        logger.warning("Owner restriction disabled — bot is open to everyone")

    # Single conversational router
    dp.include_router(chat.router)

    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
