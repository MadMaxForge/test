import aiohttp
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from calendar_bot.tools import TOOLS
from calendar_bot.calendar_api import CalendarAPI

logger = logging.getLogger(__name__)

# Category auto-detection keywords
CATEGORY_KEYWORDS = {
    "meeting": ["встреча", "созвон", "совещание", "митинг", "звонок", "собеседование", "переговоры"],
    "urgent": ["срочно", "дедлайн", "deadline", "горит", "asap"],
    "health": ["тренировка", "спорт", "врач", "доктор", "зал", "бег", "йога", "медицина"],
    "learning": ["учёба", "курс", "лекция", "вебинар", "обучение", "урок", "семинар"],
    "personal": ["обед", "ужин", "отдых", "перерыв", "пауза", "сон", "завтрак"],
    "work": ["задача", "работа", "проект", "таск", "task", "ревью", "код"],
}

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
4. Текущая дата, время и часовой пояс передаются тебе в начале каждого сообщения
5. КРИТИЧНО: Все даты/время в параметрах инструментов ОБЯЗАТЕЛЬНО указывай с часовым поясом! Формат: 2026-03-18T09:00:00+03:00 (для Москвы). НИКОГДА не отправляй время без +03:00 или другого смещения — иначе событие будет создано не в то время!

Работа с событиями — КРИТИЧЕСКИ ВАЖНО:
6. Каждое событие имеет id, title и start. Сохраняй ВСЕ ТРИ поля из ответа любого инструмента
7. НИКОГДА не придумывай ID! Используй ТОЛЬКО реальные данные из ответов инструментов
8. При удалении/обновлении/пометке ВСЕГДА передавай event_id + title + start — это нужно для надёжного поиска (особенно для повторяющихся событий)
9. Для удаления ВСЕХ событий на дату — используй delete_events_by_date (самый надёжный способ). Передай date и titles
10. Для удаления конкретных событий — используй batch_delete_events, передавая events с event_id + title + start для каждого
11. Перед массовыми операциями (удаление 3+ событий) — спроси подтверждение: "Точно удалить все 3?"

Новые возможности:
12. Цвета/категории: при создании события автоматически определяй категорию по ключевым словам. Категории: urgent (красный), meeting (синий), work (лавандовый), personal (серый), health (зелёный), learning (фиолетовый), in_progress (жёлтый), done (зелёный)
13. Для отметки задачи выполненной — используй mark_event_done. Это поставит зелёный цвет и добавит "Done:" к названию
14. Для показа выполненных задач — используй get_completed_events
15. Для повторяющихся событий — используй create_recurring_event
16. Для клонирования — используй clone_event
17. Для поиска свободного времени определённой длительности — используй find_common_free_time
18. Для статистики за неделю — используй get_week_stats

Проактивность:
19. Если видишь больше 3 просроченных задач — предложи перенести
20. Используй suggest_reschedule чтобы найти лучшее время
21. Если день перегружен — предупреди об этом"""


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
        db: Any = None,
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
                return await calendar.delete_event(
                    event_id=args["event_id"],
                    title=args.get("title"),
                    start=args.get("start"),
                )
            elif name == "delete_events_by_date":
                return await calendar.delete_events_by_date(
                    date=args["date"],
                    titles=args.get("titles"),
                )
            elif name == "batch_delete_events":
                results = []
                events_list = args.get("events", [])
                # Backward compat: support old event_ids format
                if not events_list and "event_ids" in args:
                    events_list = [{"event_id": eid} for eid in args["event_ids"]]
                for ev in events_list:
                    r = await calendar.delete_event(
                        event_id=ev.get("event_id", ""),
                        title=ev.get("title"),
                        start=ev.get("start"),
                    )
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
            elif name == "set_event_color":
                return await calendar.set_event_color(
                    event_id=args["event_id"],
                    color=args.get("color"),
                    category=args.get("category"),
                    title=args.get("title"),
                    start=args.get("start"),
                )
            elif name == "clone_event":
                return await calendar.clone_event(
                    event_id=args["event_id"],
                    new_date=args["new_date"],
                    title=args.get("title"),
                    start=args.get("start"),
                )
            elif name == "get_week_stats":
                week_result = await calendar.get_week_events()
                overdue_result = await calendar.get_overdue()
                completed_result = await calendar.get_completed_events()
                events = week_result.get("events", [])
                overdue = overdue_result.get("events", [])
                completed = completed_result.get("events", [])
                total_minutes = 0
                category_minutes: dict[str, int] = {}
                for ev in events:
                    if not ev.get("isAllDay"):
                        try:
                            s = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
                            en = datetime.fromisoformat(ev["end"].replace("Z", "+00:00"))
                            mins = int((en - s).total_seconds() / 60)
                            total_minutes += mins
                            color = ev.get("color", "")
                            cat = _color_to_category(color)
                            category_minutes[cat] = category_minutes.get(cat, 0) + mins
                        except Exception:
                            pass
                return {
                    "total_events": len(events),
                    "total_busy_hours": round(total_minutes / 60, 1),
                    "by_category_hours": {k: round(v / 60, 1) for k, v in category_minutes.items()},
                    "completed_count": len(completed),
                    "overdue_count": len(overdue),
                    "events": events,
                }
            elif name == "create_recurring_event":
                return await calendar.create_recurring_event(
                    title=args["title"],
                    start=args["start"],
                    end=args.get("end"),
                    frequency=args["frequency"],
                    count=args.get("count"),
                    description=args.get("description"),
                    category=args.get("category"),
                )
            elif name == "find_common_free_time":
                duration = args["duration_minutes"]
                days_ahead = args.get("days_ahead", 7)
                found_slots: list[dict[str, str]] = []
                for day_offset in range(days_ahead):
                    date = (datetime.now() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                    free_result = await calendar.get_free_busy(date)
                    for slot in free_result.get("free", []):
                        try:
                            s = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
                            e = datetime.fromisoformat(slot["end"].replace("Z", "+00:00"))
                            slot_minutes = int((e - s).total_seconds() / 60)
                            if slot_minutes >= duration:
                                found_slots.append({
                                    "date": date,
                                    "start": slot["start"],
                                    "end": slot["end"],
                                    "available_minutes": slot_minutes,
                                })
                        except Exception:
                            pass
                    if found_slots:
                        break
                return {
                    "requested_minutes": duration,
                    "found_slots": found_slots[:5],
                    "found_count": len(found_slots),
                }
            elif name == "mark_event_done":
                return await calendar.mark_event_done(
                    event_id=args["event_id"],
                    title=args.get("title"),
                    start=args.get("start"),
                )
            elif name == "get_completed_events":
                return await calendar.get_completed_events(
                    start=args.get("start"),
                    end=args.get("end"),
                )
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.error("Tool execution error: %s", e)
            return {"error": str(e)}


def _color_to_category(color: str) -> str:
    mapping = {
        "11": "urgent",
        "9": "meeting",
        "10": "done",
        "5": "in_progress",
        "3": "learning",
        "8": "personal",
        "2": "health",
        "1": "work",
    }
    return mapping.get(color, "other")
