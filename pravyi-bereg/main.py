"""Main entry point: starts Telegram bot + scheduler."""
from __future__ import annotations

import asyncio
import logging
import sys

from config import ensure_dirs, validate_config, TG_OWNER_CHAT_ID
from db import init_db
from content.topics import init_topics_bank
from bot.handlers import build_bot_app, send_for_approval
from scheduler import setup_schedule, get_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


async def on_startup(bot_app):
    """Actions to perform on bot startup."""
    log.info("Bot starting up...")

    # Notify owner
    if TG_OWNER_CHAT_ID:
        try:
            await bot_app.bot.send_message(
                chat_id=TG_OWNER_CHAT_ID,
                text=(
                    "🟢 *Правый Берег — бот запущен!*\n\n"
                    "Расписание:\n"
                    "📝 Вт 19:00 — пост\n"
                    "🎬 Чт 19:00 — рилс\n"
                    "📝 Сб 10:00 — пост\n"
                    "🎬 Вс 10:00 — рилс\n\n"
                    "Команды: /help"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error("Failed to send startup message: %s", e)


async def run_initial_parse():
    """Run initial competitor parsing on first launch."""
    from content.parser import parse_all_competitors, generate_topics_from_parsed
    from content.topics import get_topic_stats

    stats = get_topic_stats()
    # Only parse if we have few topics (first launch)
    if stats["total"] < 80:
        log.info("Running initial competitor parsing...")
        results = await parse_all_competitors()
        log.info("Parsed competitors: %s", results)

        new_topics = await generate_topics_from_parsed(count=15)
        log.info("Generated %d AI topics", len(new_topics))

        stats = get_topic_stats()
        log.info("Topics bank now has %d topics", stats["total"])


def main():
    """Main entry point."""
    log.info("=" * 60)
    log.info("Правый Берег — VK Automation Bot")
    log.info("=" * 60)

    # 1. Ensure directories exist
    ensure_dirs()

    # 2. Validate config
    warnings = validate_config()
    if warnings:
        for w in warnings:
            log.warning("Config: %s", w)

    # 3. Initialize database
    init_db()

    # 4. Initialize topics bank
    init_topics_bank()

    # 5. Build bot application
    bot_app = build_bot_app()

    # 6. Setup scheduler
    scheduler = setup_schedule(bot_app)

    # 7. Register startup callback
    async def post_init(application):
        await on_startup(application)
        # Start scheduler
        scheduler.start()
        log.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))
        # Run initial parsing in background
        asyncio.create_task(run_initial_parse())

    bot_app.post_init = post_init

    # 8. Run bot (this blocks until stopped)
    log.info("Starting bot polling...")
    bot_app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
