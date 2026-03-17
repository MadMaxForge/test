import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from calendar_bot.calendar_api import CalendarAPI
from calendar_bot.database import Database
from calendar_bot.config import Config

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


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
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

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
                timezone="Europe/Moscow",
            ),
            id="daily_digest",
            replace_existing=True,
        )

        # Weekly cleanup of old reminders
        self.scheduler.add_job(
            self._cleanup,
            CronTrigger(day_of_week="mon", hour=3, minute=0, timezone="Europe/Moscow"),
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
            now = datetime.now(MSK)

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
                    ).astimezone(MSK)
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
            now = datetime.now(MSK)
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
                        ).astimezone(MSK)
                        end = datetime.fromisoformat(
                            event["end"].replace("Z", "+00:00")
                        ).astimezone(MSK)
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
                    ).astimezone(MSK)
                    msg += f"\n• {event['title']} (закончилось {end.strftime('%d.%m %H:%M')})"

            await self.send_message(self.config.owner_chat_id, msg)
            logger.info("Daily digest sent")

        except Exception as e:
            logger.error("Daily digest error: %s", e)

    async def _cleanup(self) -> None:
        try:
            await self.db.cleanup_old_reminders()
            logger.info("Old reminders cleaned up")
        except Exception as e:
            logger.error("Cleanup error: %s", e)
