import aiohttp
import json
import logging
from typing import Any

from calendar_bot.tools import TOOLS
from calendar_bot.calendar_api import CalendarAPI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — Джарвис, персональный календарный ассистент в Telegram. Ты помогаешь управлять Google Календарём.

Твои возможности:
- Показывать расписание на сегодня, завтра, неделю
- Создавать новые события
- Переносить и редактировать события
- Удалять события
- Искать события по названию
- Показывать свободные слоты
- Напоминать о просроченных задачах

Правила:
1. Отвечай на русском языке, кратко и по делу
2. Используй эмодзи для наглядности (📅 для дат, ⏰ для времени, ✅ для выполненных действий)
3. При отображении событий форматируй их красиво и читаемо
4. Если пользователь просит создать событие без указания времени окончания, ставь длительность 1 час по умолчанию
5. Если пользователь просит перенести событие, сначала найди его через search_events, потом используй update_event
6. Даты и время указывай в московском часовом поясе (UTC+3)
7. Текущая дата и время передаются тебе в каждом сообщении
8. Если нужно удалить или обновить событие, всегда сначала найди его ID через поиск или получение списка событий
9. Будь проактивным — если видишь просроченные задачи, предупреждай о них"""


class LLMService:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.session: aiohttp.ClientSession | None = None

    async def init(self) -> None:
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def process_message(
        self,
        user_message: str,
        current_time: str,
        calendar: CalendarAPI,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if history:
            messages.extend(history)

        messages.append(
            {
                "role": "user",
                "content": f"[Текущее время: {current_time}]\n\n{user_message}",
            }
        )

        # Allow up to 5 rounds of tool calls
        for _ in range(5):
            response = await self._call_llm(messages)
            if not response:
                return "Произошла ошибка при обращении к AI. Попробуй ещё раз."

            message = response["choices"][0]["message"]

            if message.get("tool_calls"):
                messages.append(message)
                for tool_call in message["tool_calls"]:
                    result = await self._execute_tool(
                        tool_call["function"]["name"],
                        json.loads(tool_call["function"]["arguments"]),
                        calendar,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
            else:
                return message.get("content", "Не удалось получить ответ.")

        return "Слишком много шагов обработки. Попробуй сформулировать запрос проще."

    async def _call_llm(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        assert self.session is not None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 2000,
        }

        try:
            async with self.session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("LLM API error %d: %s", resp.status, text)
                    return None
                return await resp.json()
        except Exception as e:
            logger.error("LLM API exception: %s", e)
            return None

    async def _execute_tool(
        self, name: str, args: dict[str, Any], calendar: CalendarAPI
    ) -> dict[str, Any]:
        logger.info("Executing tool: %s(%s)", name, args)
        try:
            if name == "get_today_events":
                return await calendar.get_today_events()
            elif name == "get_tomorrow_events":
                return await calendar.get_tomorrow_events()
            elif name == "get_week_events":
                return await calendar.get_week_events()
            elif name == "get_events_for_period":
                return await calendar.get_events(args["start"], args["end"])
            elif name == "create_event":
                return await calendar.create_event(
                    title=args["title"],
                    start=args["start"],
                    end=args.get("end"),
                    description=args.get("description"),
                    location=args.get("location"),
                    all_day=args.get("all_day", False),
                )
            elif name == "update_event":
                return await calendar.update_event(
                    event_id=args["event_id"],
                    title=args.get("title"),
                    start=args.get("start"),
                    end=args.get("end"),
                    description=args.get("description"),
                )
            elif name == "delete_event":
                return await calendar.delete_event(args["event_id"])
            elif name == "search_events":
                return await calendar.search_events(
                    args["query"], args.get("days", 30)
                )
            elif name == "get_free_slots":
                return await calendar.get_free_busy(args.get("date"))
            elif name == "get_overdue_events":
                return await calendar.get_overdue()
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.error("Tool execution error: %s", e)
            return {"error": str(e)}
