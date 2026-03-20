import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
bot_enabled: bool = True


async def send_message_to_user(chat_id: int, text: str) -> None:
    if not bot_enabled:
        return
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Failed to send message to %d: %s", chat_id, e)


def is_owner(message: types.Message) -> bool:
    if config.owner_chat_id == 0:
        return True
    return message.chat.id == config.owner_chat_id


@dp.message(Command("on"))
async def cmd_on(message: types.Message) -> None:
    if not is_owner(message):
        return
    global bot_enabled
    bot_enabled = True
    await db.set_setting("bot_enabled", "1")
    if scheduler:
        scheduler.enabled = True
    logger.info("Bot ENABLED by owner")
    await message.answer(
        "\u2705 <b>\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u0438\u043a \u0432\u043a\u043b\u044e\u0447\u0451\u043d!</b>\n\n"
        "\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439, \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f \u0438 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442\u044b \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0442.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("off"))
async def cmd_off(message: types.Message) -> None:
    if not is_owner(message):
        return
    global bot_enabled
    bot_enabled = False
    await db.set_setting("bot_enabled", "0")
    if scheduler:
        scheduler.enabled = False
    logger.info("Bot DISABLED by owner")
    await message.answer(
        "\u23f8 <b>\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u0438\u043a \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d.</b>\n\n"
        "\u0422\u043e\u043a\u0435\u043d\u044b \u043d\u0435 \u0442\u0440\u0430\u0442\u044f\u0442\u0441\u044f. \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f \u0438 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442\u044b \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u044b.\n"
        "\u0427\u0442\u043e\u0431\u044b \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u043e\u0431\u0440\u0430\u0442\u043d\u043e \u2014 /on",
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "bot_on")
async def callback_on(callback: types.CallbackQuery) -> None:
    if config.owner_chat_id != 0 and callback.message and callback.message.chat.id != config.owner_chat_id:
        return
    global bot_enabled
    bot_enabled = True
    await db.set_setting("bot_enabled", "1")
    if scheduler:
        scheduler.enabled = True
    logger.info("Bot ENABLED via button")
    if callback.message:
        await callback.message.edit_text(
            "\u2705 <b>\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u0438\u043a \u0432\u043a\u043b\u044e\u0447\u0451\u043d!</b>\n\n"
            "\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439, \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f \u0438 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442\u044b \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0442.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="\u23f8 \u0412\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c", callback_data="bot_off")]
            ]),
        )
    await callback.answer("\u0412\u043a\u043b\u044e\u0447\u0435\u043d\u043e!")


@dp.callback_query(F.data == "bot_off")
async def callback_off(callback: types.CallbackQuery) -> None:
    if config.owner_chat_id != 0 and callback.message and callback.message.chat.id != config.owner_chat_id:
        return
    global bot_enabled
    bot_enabled = False
    await db.set_setting("bot_enabled", "0")
    if scheduler:
        scheduler.enabled = False
    logger.info("Bot DISABLED via button")
    if callback.message:
        await callback.message.edit_text(
            "\u23f8 <b>\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u0438\u043a \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d.</b>\n\n"
            "\u0422\u043e\u043a\u0435\u043d\u044b \u043d\u0435 \u0442\u0440\u0430\u0442\u044f\u0442\u0441\u044f. \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f \u0438 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442\u044b \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u044b.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="\u2705 \u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c", callback_data="bot_on")]
            ]),
        )
    await callback.answer("\u0412\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e!")


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
    status = "✅ Включён" if bot_enabled else "⏸ Выключен"
    await message.answer(
        f"📖 <b>Команды:</b>\n\n"
        "/today — расписание на сегодня\n"
        "/tomorrow — расписание на завтра\n"
        "/week — расписание на неделю\n"
        "/free — свободные слоты на сегодня\n"
        "/overdue — просроченные задачи\n"
        "/on — включить бота\n"
        "/off — выключить бота (токены не тратятся)\n"
        "/help — эта справка\n\n"
        f"Статус: {status}\n\n"
        "Или просто пиши обычным текстом!",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text)
async def on_message(message: types.Message) -> None:
    if not is_owner(message):
        await message.answer("⛔ Этот бот приватный.")
        return

    if not bot_enabled:
        await message.answer(
            "⏸ Календарик сейчас выключен. Нажми кнопку ниже или /on чтобы включить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Включить", callback_data="bot_on")]
            ]),
        )
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

    # Restore bot_enabled state from DB
    global bot_enabled
    saved_enabled = await db.get_setting("bot_enabled")
    if saved_enabled is not None:
        bot_enabled = saved_enabled == "1"
        logger.info("Restored bot_enabled: %s", bot_enabled)

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
