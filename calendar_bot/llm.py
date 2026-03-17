import aiohttp
import json
import logging
from datetime import datetime
from typing import Any

from calendar_bot.tools import TOOLS
from calendar_bot.calendar_api import CalendarAPI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — Джарвис, умный и немного ироничный персональный помощник. Ты управляешь Google Календарём через Telegram.

Твоя личность:
- Общайся как умный друг — на "ты", дружелюбно, но по делу
- Будь слегка ироничным, но не переигрывай
- Учитывай время суток: утром — "Доброе утро!", ночью — "Поздно работаешь?", вечером — "Как прошёл день?"
- Если видишь перегруженный день — подскажи: "У тебя 8 часов задач и ни одного перерыва. Может добавим обед?"
- Если видишь просроченные задачи — предложи помощь: "У тебя скопились задачи, давай разгребём?"

Основные правила:
1. Отвечай на русском, кратко и по делу. Используй эмодзи умеренно
2. Форматируй расписание красиво и читаемо (HTML-разметка: <b>, <i>, <code>)
3. Если пользователь создаёт событие без времени окончания — ставь 1 час по умолчанию
4. Даты и время — в часовом поясе пользователя (передаётся в каждом сообщении)
5. Текущая дата и время передаются тебе в начале каждого сообщения

Работа с событиями — ВАЖНО:
6. Если ты только что получил список событий (get_today_events, get_week_events и т.д.) — запоминай их ID! Не ищи заново через search_events то, что уже получил
7. Для удаления/обновления используй ID из уже полученных результатов. Вызывай search_events только если ID неизвестен
8. Для массового удаления используй batch_delete_events — это быстрее и дешевле, чем удалять по одному
9. Для переноса события используй move_event с event_id и новым временем
10. Перед массовыми операциями (удаление 3+ событий) — спроси подтверждение: "Точно удалить все 3?"
11. Для сводки дня используй get_day_summary — он покажет количество задач, свободное время и просрочки одним запросом

Проактивность:
12. Если видишь больше 3 просроченных задач — предложи перенести: "У тебя 5 просроченных. Давай найдём свободные слоты и перенесём?"
13. Используй suggest_reschedule чтобы найти лучшее время для просроченных задач
14. Если день перегружен — предупреди об этом"""


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
            elif name == "batch_delete_events":
                results = []
                for eid in args["event_ids"]:
                    r = await calendar.delete_event(eid)
                    results.append(r)
                deleted = [r.get("deleted", "") for r in results if r.get("success")]
                errors = [r.get("error", "") for r in results if r.get("error")]
                return {
                    "deleted": deleted,
                    "deleted_count": len(deleted),
                    "errors": errors,
                }
            elif name == "move_event":
                return await calendar.update_event(
                    event_id=args["event_id"],
                    start=args["new_start"],
                    end=args.get("new_end"),
                )
            elif name == "get_day_summary":
                date = args.get("date")
                if date:
                    start = f"{date}T00:00:00"
                    end = f"{date}T23:59:59"
                    events_result = await calendar.get_events(start, end)
                else:
                    events_result = await calendar.get_today_events()
                free_result = await calendar.get_free_busy(date)
                overdue_result = await calendar.get_overdue()
                events = events_result.get("events", [])
                free_slots = free_result.get("free", [])
                overdue = overdue_result.get("events", [])
                total_busy_minutes = 0
                for e in events:
                    if not e.get("isAllDay"):
                        try:
                            s = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
                            en = datetime.fromisoformat(e["end"].replace("Z", "+00:00"))
                            total_busy_minutes += int((en - s).total_seconds() / 60)
                        except Exception:
                            pass
                return {
                    "events_count": len(events),
                    "events": events,
                    "free_slots": free_slots,
                    "free_slots_count": len(free_slots),
                    "busy_minutes": total_busy_minutes,
                    "busy_hours": round(total_busy_minutes / 60, 1),
                    "overdue_count": len(overdue),
                    "overdue": overdue[:5],
                }
            elif name == "suggest_reschedule":
                overdue_result = await calendar.get_overdue()
                overdue = overdue_result.get("events", [])
                target_date = args.get("target_date")
                free_result = await calendar.get_free_busy(target_date)
                free_slots = free_result.get("free", [])
                return {
                    "overdue_events": overdue[:10],
                    "overdue_count": len(overdue),
                    "free_slots": free_slots,
                    "target_date": target_date or "today",
                }
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
