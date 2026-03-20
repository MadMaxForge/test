import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from calendar_bot.calendar_api import CalendarAPI
from calendar_bot.database import Database
from calendar_bot.config import Config

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        config: Config,
        calendar: CalendarAPI,
        db: Database,
        send_message_func,
    ):
        self.config = config
        self.calendar = calendar
        self.db = db
        self.send_message = send_message_func
        self.tz = ZoneInfo(config.timezone)
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)
        self.enabled: bool = True

    def start(self) -> None:
        # Check for upcoming events periodically
        self.scheduler.add_job(
            self._check_reminders,
            IntervalTrigger(seconds=self.config.reminder_check_interval),
            id="check_reminders",
            replace_existing=True,
        )

        # Daily morning digest
        self.scheduler.add_job(
            self._send_daily_digest,
            CronTrigger(
                hour=self.config.daily_digest_hour,
                minute=self.config.daily_digest_minute,
                timezone=self.config.timezone,
            ),
            id="daily_digest",
            replace_existing=True,
        )

        # Check overdue tasks periodically (every 4 hours)
        self.scheduler.add_job(
            self._check_overdue,
            CronTrigger(
                hour="10,14,18",
                minute=0,
                timezone=self.config.timezone,
            ),
            id="check_overdue",
            replace_existing=True,
        )

        # Evening report at 21:00
        self.scheduler.add_job(
            self._send_evening_report,
            CronTrigger(
                hour=21,
                minute=0,
                timezone=self.config.timezone,
            ),
            id="evening_report",
            replace_existing=True,
        )

        # Weekly cleanup of old reminders
        self.scheduler.add_job(
            self._cleanup,
            CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=self.config.timezone),
            id="cleanup",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    async def _check_reminders(self) -> None:
        if not self.enabled or self.config.owner_chat_id == 0:
            return

        try:
            now = datetime.now(self.tz)

            for minutes_before in self.config.reminder_before_minutes:
                result = await self.calendar.get_upcoming(minutes=minutes_before)
                if "error" in result:
                    logger.error("Error fetching upcoming events: %s", result["error"])
                    continue

                events = result.get("events", [])
                for event in events:
                    event_id = event["id"]
                    reminder_type = f"before_{minutes_before}"

                    if await self.db.is_reminder_sent(event_id, reminder_type):
                        continue

                    start_time = datetime.fromisoformat(
                        event["start"].replace("Z", "+00:00")
                    ).astimezone(self.tz)
                    time_str = start_time.strftime("%H:%M")

                    if event.get("isAllDay"):
                        msg = f"📅 Напоминание: <b>{event['title']}</b>\n🗓 Весь день"
                    else:
                        msg = (
                            f"⏰ Напоминание (через {minutes_before} мин):\n"
                            f"📅 <b>{event['title']}</b>\n"
                            f"🕐 {time_str}"
                        )

                    if event.get("location"):
                        msg += f"\n📍 {event['location']}"
                    if event.get("description"):
                        msg += f"\n📝 {event['description']}"

                    await self.send_message(self.config.owner_chat_id, msg)
                    await self.db.mark_reminder_sent(event_id, reminder_type)
                    logger.info("Sent reminder for event: %s", event["title"])

        except Exception as e:
            logger.error("Reminder check error: %s", e)

    async def _send_daily_digest(self) -> None:
        if not self.enabled or self.config.owner_chat_id == 0:
            return

        try:
            result = await self.calendar.get_today_events()
            if "error" in result:
                logger.error("Error fetching today events: %s", result["error"])
                return

            events = result.get("events", [])
            now = datetime.now(self.tz)
            date_str = now.strftime("%d.%m.%Y")
            weekday = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][now.weekday()]

            if not events:
                msg = f"☀️ Доброе утро! {weekday}, {date_str}\n\n📭 На сегодня событий нет. Свободный день! Можешь наконец отдохнуть 😌"
            else:
                msg = f"☀️ Доброе утро! {weekday}, {date_str}\n\n📋 <b>Расписание на сегодня:</b>\n"
                total_busy_minutes = 0
                gaps: list[tuple[datetime, datetime]] = []
                sorted_events = []

                for event in events:
                    if event.get("isAllDay"):
                        continue
                    try:
                        start = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).astimezone(self.tz)
                        end = datetime.fromisoformat(event["end"].replace("Z", "+00:00")).astimezone(self.tz)
                        mins = int((end - start).total_seconds() / 60)
                        total_busy_minutes += mins
                        sorted_events.append((start, end, event))
                    except Exception:
                        pass

                sorted_events.sort(key=lambda x: x[0])

                for i, event in enumerate(events, 1):
                    color_emoji = _color_emoji(event.get("color", ""))
                    if event.get("isAllDay"):
                        msg += f"\n{i}. 🗓 <b>{event['title']}</b> (весь день)"
                    else:
                        start = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).astimezone(self.tz)
                        end = datetime.fromisoformat(event["end"].replace("Z", "+00:00")).astimezone(self.tz)
                        msg += f"\n{i}. {color_emoji} {start.strftime('%H:%M')}-{end.strftime('%H:%M')} — <b>{event['title']}</b>"

                    if event.get("location"):
                        msg += f"\n   📍 {event['location']}"

                # Workload forecast
                busy_hours = round(total_busy_minutes / 60, 1)
                msg += f"\n\n📊 <b>Прогноз нагрузки:</b> {busy_hours}ч задач из ~14ч рабочего дня"

                # Find gaps between events for break recommendations
                if sorted_events:
                    for j in range(len(sorted_events) - 1):
                        gap_start = sorted_events[j][1]
                        gap_end = sorted_events[j + 1][0]
                        gap_minutes = int((gap_end - gap_start).total_seconds() / 60)
                        if gap_minutes >= 30:
                            gaps.append((gap_start, gap_end))

                    if gaps:
                        best_gap = max(gaps, key=lambda g: (g[1] - g[0]).total_seconds())
                        msg += f"\n💡 Окно для перерыва: {best_gap[0].strftime('%H:%M')}–{best_gap[1].strftime('%H:%M')}"
                    elif busy_hours >= 6:
                        msg += "\n⚠️ Плотный день без перерывов! Постарайся найти время на отдых."

            # Check overdue
            overdue_result = await self.calendar.get_overdue()
            overdue_events = overdue_result.get("events", [])
            if overdue_events:
                msg += f"\n\n⚠️ <b>Просроченных задач: {len(overdue_events)}</b>"
                for event in overdue_events[:3]:
                    end = datetime.fromisoformat(event["end"].replace("Z", "+00:00")).astimezone(self.tz)
                    msg += f"\n• {event['title']} (с {end.strftime('%d.%m')})"
                if len(overdue_events) > 3:
                    msg += f"\n...и ещё {len(overdue_events) - 3}"
                msg += "\nНапиши мне — помогу перенести!"

            await self.send_message(self.config.owner_chat_id, msg)
            logger.info("Daily digest v2 sent")

        except Exception as e:
            logger.error("Daily digest error: %s", e)

    async def _check_overdue(self) -> None:
        if not self.enabled or self.config.owner_chat_id == 0:
            return

        try:
            result = await self.calendar.get_overdue()
            if "error" in result:
                logger.error("Error fetching overdue events: %s", result["error"])
                return

            overdue_events = result.get("events", [])
            if len(overdue_events) < 3:
                return

            # Check if we already notified about overdue today
            today_key = f"overdue_notified_{datetime.now(self.tz).strftime('%Y-%m-%d')}"
            if await self.db.is_reminder_sent(today_key, "overdue_check"):
                return

            # Get free slots for today
            free_result = await self.calendar.get_free_busy()
            free_slots = free_result.get("free", [])

            msg = (
                f"\u26a0\ufe0f <b>\u041d\u0430\u043a\u043e\u043f\u0438\u043b\u043e\u0441\u044c {len(overdue_events)} \u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043d\u044b\u0445 \u0437\u0430\u0434\u0430\u0447!</b>\n\n"
            )

            for i, event in enumerate(overdue_events[:5], 1):
                end = datetime.fromisoformat(
                    event["end"].replace("Z", "+00:00")
                ).astimezone(self.tz)
                msg += f"{i}. {event['title']} <i>(\u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043e {end.strftime('%d.%m')})</i>\n"

            if len(overdue_events) > 5:
                msg += f"\n...\u0438 \u0435\u0449\u0451 {len(overdue_events) - 5}\n"

            if free_slots:
                msg += "\n\U0001f4a1 <b>\u0421\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0435 \u0441\u043b\u043e\u0442\u044b \u0441\u0435\u0433\u043e\u0434\u043d\u044f:</b>\n"
                for slot in free_slots[:4]:
                    try:
                        s = datetime.fromisoformat(slot["start"].replace("Z", "+00:00")).astimezone(self.tz)
                        e = datetime.fromisoformat(slot["end"].replace("Z", "+00:00")).astimezone(self.tz)
                        msg += f"  \u2022 {s.strftime('%H:%M')}\u2013{e.strftime('%H:%M')}\n"
                    except Exception:
                        pass

            msg += "\n\u041d\u0430\u043f\u0438\u0448\u0438 \u043c\u043d\u0435, \u0435\u0441\u043b\u0438 \u0445\u043e\u0447\u0435\u0448\u044c \u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0442\u0438 \u0437\u0430\u0434\u0430\u0447\u0438 \u2014 \u043f\u043e\u043c\u043e\u0433\u0443 \u043d\u0430\u0439\u0442\u0438 \u043b\u0443\u0447\u0448\u0435\u0435 \u0432\u0440\u0435\u043c\u044f! \U0001f4aa"

            await self.send_message(self.config.owner_chat_id, msg)
            await self.db.mark_reminder_sent(today_key, "overdue_check")
            logger.info("Overdue notification sent: %d tasks", len(overdue_events))

        except Exception as e:
            logger.error("Overdue check error: %s", e)

    async def _send_evening_report(self) -> None:
        if not self.enabled or self.config.owner_chat_id == 0:
            return

        try:
            now = datetime.now(self.tz)

            # Get today's events
            today_result = await self.calendar.get_today_events()
            today_events = today_result.get("events", [])

            # Get completed events
            completed_result = await self.calendar.get_completed_events(
                start=now.strftime("%Y-%m-%dT00:00:00"),
                end=now.strftime("%Y-%m-%dT23:59:59"),
            )
            completed = completed_result.get("events", [])

            # Get overdue
            overdue_result = await self.calendar.get_overdue()
            overdue = overdue_result.get("events", [])

            # Get tomorrow's events
            tomorrow_result = await self.calendar.get_tomorrow_events()
            tomorrow_events = tomorrow_result.get("events", [])

            msg = "🌙 <b>Вечерний отчёт</b>\n\n"

            # Done today
            if completed:
                msg += f"✅ <b>Выполнено сегодня: {len(completed)}</b>\n"
                for ev in completed[:5]:
                    title = ev["title"].replace("Done: ", "")
                    msg += f"  • {title}\n"
            else:
                msg += "📝 Выполненных задач за сегодня нет\n"

            # Overdue
            if overdue:
                msg += f"\n⚠️ <b>Просрочено: {len(overdue)}</b>\n"
                for ev in overdue[:3]:
                    end = datetime.fromisoformat(ev["end"].replace("Z", "+00:00")).astimezone(self.tz)
                    msg += f"  • {ev['title']} (с {end.strftime('%d.%m')})\n"
            else:
                msg += "\n🎉 Просроченных задач нет!\n"

            # Tomorrow preview
            if tomorrow_events:
                msg += f"\n📅 <b>Завтра: {len(tomorrow_events)} событий</b>\n"
                for ev in tomorrow_events[:5]:
                    if ev.get("isAllDay"):
                        msg += f"  • 🗓 {ev['title']} (весь день)\n"
                    else:
                        start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone(self.tz)
                        msg += f"  • {start.strftime('%H:%M')} {ev['title']}\n"
            else:
                msg += "\n📅 Завтра свободный день\n"

            msg += "\nХорошего вечера! 🌟"

            await self.send_message(self.config.owner_chat_id, msg)
            logger.info("Evening report sent")

        except Exception as e:
            logger.error("Evening report error: %s", e)

    async def _cleanup(self) -> None:
        try:
            await self.db.cleanup_old_reminders()
            logger.info("Old reminders cleaned up")
        except Exception as e:
            logger.error("Cleanup error: %s", e)


def _color_emoji(color: str) -> str:
    return {
        "11": "🔴",  # urgent
        "9": "🔵",   # meeting
        "10": "🟢",  # done
        "5": "🟡",   # in_progress
        "3": "🟣",   # learning
        "8": "⚪",   # personal
        "2": "🟢",   # health
        "1": "🔵",   # work
    }.get(color, "🕐")
