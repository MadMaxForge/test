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
        if self.config.owner_chat_id == 0:
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
        if self.config.owner_chat_id == 0:
            return

        try:
            result = await self.calendar.get_today_events()
            if "error" in result:
                logger.error("Error fetching today events: %s", result["error"])
                return

            events = result.get("events", [])
            now = datetime.now(self.tz)
            date_str = now.strftime("%d.%m.%Y")

            if not events:
                msg = f"☀️ Доброе утро! Сегодня {date_str}\n\n📭 На сегодня событий нет. Свободный день!"
            else:
                msg = f"☀️ Доброе утро! Сегодня {date_str}\n\n📋 <b>Расписание на сегодня:</b>\n"
                for i, event in enumerate(events, 1):
                    if event.get("isAllDay"):
                        msg += f"\n{i}. 🗓 <b>{event['title']}</b> (весь день)"
                    else:
                        start = datetime.fromisoformat(
                            event["start"].replace("Z", "+00:00")
                        ).astimezone(self.tz)
                        end = datetime.fromisoformat(
                            event["end"].replace("Z", "+00:00")
                        ).astimezone(self.tz)
                        msg += f"\n{i}. 🕐 {start.strftime('%H:%M')}-{end.strftime('%H:%M')} — <b>{event['title']}</b>"

                    if event.get("location"):
                        msg += f"\n   📍 {event['location']}"

                msg += f"\n\n📊 Всего событий: {len(events)}"

            # Check overdue
            overdue_result = await self.calendar.get_overdue()
            overdue_events = overdue_result.get("events", [])
            if overdue_events:
                msg += f"\n\n⚠️ <b>Просроченные задачи ({len(overdue_events)}):</b>"
                for event in overdue_events[:5]:
                    end = datetime.fromisoformat(
                        event["end"].replace("Z", "+00:00")
                    ).astimezone(self.tz)
                    msg += f"\n• {event['title']} (закончилось {end.strftime('%d.%m %H:%M')})"

            await self.send_message(self.config.owner_chat_id, msg)
            logger.info("Daily digest sent")

        except Exception as e:
            logger.error("Daily digest error: %s", e)

    async def _check_overdue(self) -> None:
        if self.config.owner_chat_id == 0:
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

    async def _cleanup(self) -> None:
        try:
            await self.db.cleanup_old_reminders()
            logger.info("Old reminders cleaned up")
        except Exception as e:
            logger.error("Cleanup error: %s", e)
