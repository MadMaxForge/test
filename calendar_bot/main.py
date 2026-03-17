import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode

from calendar_bot.config import Config
from calendar_bot.database import Database
from calendar_bot.calendar_api import CalendarAPI
from calendar_bot.llm import LLMService
from calendar_bot.scheduler import ReminderScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

config = Config.from_env()
TZ = ZoneInfo(config.timezone)
bot = Bot(token=config.telegram_token)
dp = Dispatcher()
db = Database(config.db_path)
calendar_api = CalendarAPI(config.google_script_url, config.google_script_token)
llm = LLMService(config.openrouter_api_key, config.llm_model)
scheduler: ReminderScheduler | None = None


async def send_message_to_user(chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Failed to send message to %d: %s", chat_id, e)


def is_owner(message: types.Message) -> bool:
    if config.owner_chat_id == 0:
        return True
    return message.chat.id == config.owner_chat_id


@dp.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    if config.owner_chat_id == 0:
        # Auto-register first user as owner
        config.owner_chat_id = message.chat.id
        await db.set_setting("owner_chat_id", str(message.chat.id))
        logger.info("Owner registered: %d", message.chat.id)

    if not is_owner(message):
        await message.answer("⛔ Этот бот приватный.")
        return

    await message.answer(
        "👋 Привет! Я <b>Джарвис</b> — твой календарный ассистент.\n\n"
        "Я умею:\n"
        "📅 Показывать расписание\n"
        "✏️ Создавать события (с авто-цветами по категориям)\n"
        "🔄 Переносить и клонировать события\n"
        "🗑 Удалять события\n"
        "🔍 Искать события\n"
        "🔁 Создавать повторяющиеся события\n"
        "✅ Отмечать задачи выполненными\n"
        "📊 Показывать статистику за неделю\n"
        "⏰ Напоминать о предстоящих событиях\n"
        "⚠️ Предупреждать о просроченных задачах\n"
        "☀️ Утренний дайджест в 8:00\n"
        "🌙 Вечерний отчёт в 21:00\n\n"
        "Просто пиши мне обычным текстом, например:\n"
        "• <i>Что у меня сегодня?</i>\n"
        "• <i>Создай встречу на завтра в 15:00</i>\n"
        "• <i>Отметь тренировку выполненной</i>\n"
        "• <i>Покажи выполненные задачи</i>\n"
        "• <i>Когда у меня есть 2 свободных часа?</i>\n"
        "• <i>Статистика за неделю</i>\n\n"
        f"🆔 Твой Chat ID: <code>{message.chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("today"))
async def cmd_today(message: types.Message) -> None:
    if not is_owner(message):
        return
    await handle_text(message, "Покажи расписание на сегодня")


@dp.message(Command("tomorrow"))
async def cmd_tomorrow(message: types.Message) -> None:
    if not is_owner(message):
        return
    await handle_text(message, "Покажи расписание на завтра")


@dp.message(Command("week"))
async def cmd_week(message: types.Message) -> None:
    if not is_owner(message):
        return
    await handle_text(message, "Покажи расписание на неделю")


@dp.message(Command("free"))
async def cmd_free(message: types.Message) -> None:
    if not is_owner(message):
        return
    await handle_text(message, "Когда я свободен сегодня?")


@dp.message(Command("overdue"))
async def cmd_overdue(message: types.Message) -> None:
    if not is_owner(message):
        return
    await handle_text(message, "Покажи просроченные задачи")


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    if not is_owner(message):
        return
    await message.answer(
        "📖 <b>Команды:</b>\n\n"
        "/today — расписание на сегодня\n"
        "/tomorrow — расписание на завтра\n"
        "/week — расписание на неделю\n"
        "/free — свободные слоты на сегодня\n"
        "/overdue — просроченные задачи\n"
        "/help — эта справка\n\n"
        "Или просто пиши обычным текстом!",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text)
async def on_message(message: types.Message) -> None:
    if not is_owner(message):
        await message.answer("⛔ Этот бот приватный.")
        return

    text = message.text
    if not text:
        return

    await handle_text(message, text)


async def handle_text(message: types.Message, text: str) -> None:
    typing_task = asyncio.create_task(keep_typing(message))

    try:
        history = await db.get_history(message.chat.id, limit=6)
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S (%Z)")

        response = await llm.process_message(
            user_message=text,
            current_time=now,
            calendar=calendar_api,
            history=history,
        )

        if not response:
            response = "Не удалось получить ответ. Попробуй ещё раз."

        await db.save_message(message.chat.id, "user", text)
        await db.save_message(message.chat.id, "assistant", response)

        # Split long messages (Telegram limit is 4096 chars)
        if len(response) > 4000:
            parts = split_message(response, 4000)
            for part in parts:
                await message.answer(part, parse_mode=ParseMode.HTML)
        else:
            await message.answer(response, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("Error processing message: %s", e)
        await message.answer(
            "😔 Произошла ошибка при обработке запроса. Попробуй ещё раз."
        )
    finally:
        typing_task.cancel()


async def keep_typing(message: types.Message) -> None:
    try:
        while True:
            await message.answer_chat_action("typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


def split_message(text: str, max_length: int) -> list[str]:
    parts: list[str] = []
    while len(text) > max_length:
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        parts.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


async def main() -> None:
    logger.info("Starting Calendar Bot...")

    await db.init()
    await calendar_api.init()
    await llm.init()

    # Restore owner_chat_id from DB if not set
    if config.owner_chat_id == 0:
        saved_owner = await db.get_setting("owner_chat_id")
        if saved_owner:
            config.owner_chat_id = int(saved_owner)
            logger.info("Restored owner chat ID: %d", config.owner_chat_id)

    global scheduler
    scheduler = ReminderScheduler(config, calendar_api, db, send_message_to_user)
    scheduler.start()

    logger.info("Bot is running! Owner chat ID: %d", config.owner_chat_id)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.stop()
        await calendar_api.close()
        await llm.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
