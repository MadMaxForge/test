"""Scheduler: generate content on schedule and send for approval."""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import TIMEZONE

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot_app = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    return _scheduler


def setup_schedule(bot_app):
    """Set up the content generation schedule.
    
    Schedule:
    - Tuesday 19:00 MSK — post (text + photo cover)
    - Thursday 19:00 MSK — reel (video + TTS)
    - Saturday 10:00 MSK — post
    - Sunday 10:00 MSK — reel
    
    Content is generated 2 hours BEFORE the scheduled time,
    sent to TG for approval, then published at the scheduled time if approved.
    """
    global _bot_app
    _bot_app = bot_app
    
    scheduler = get_scheduler()
    
    # Generate posts 2 hours before publish time
    # Tuesday: generate at 17:00, publish at 19:00
    scheduler.add_job(
        _scheduled_generate,
        CronTrigger(day_of_week="tue", hour=17, minute=0, timezone=TIMEZONE),
        args=["post"],
        id="gen_tue_post",
        name="Generate Tuesday post",
        replace_existing=True,
    )
    
    # Thursday: generate at 17:00, publish at 19:00
    scheduler.add_job(
        _scheduled_generate,
        CronTrigger(day_of_week="thu", hour=17, minute=0, timezone=TIMEZONE),
        args=["reel"],
        id="gen_thu_reel",
        name="Generate Thursday reel",
        replace_existing=True,
    )
    
    # Saturday: generate at 8:00, publish at 10:00
    scheduler.add_job(
        _scheduled_generate,
        CronTrigger(day_of_week="sat", hour=8, minute=0, timezone=TIMEZONE),
        args=["post"],
        id="gen_sat_post",
        name="Generate Saturday post",
        replace_existing=True,
    )
    
    # Sunday: generate at 8:00, publish at 10:00
    scheduler.add_job(
        _scheduled_generate,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=TIMEZONE),
        args=["reel"],
        id="gen_sun_reel",
        name="Generate Sunday reel",
        replace_existing=True,
    )
    
    # Weekly competitor parsing (Monday 6:00 MSK)
    scheduler.add_job(
        _scheduled_parse_competitors,
        CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=TIMEZONE),
        id="parse_competitors",
        name="Parse competitors weekly",
        replace_existing=True,
    )
    
    log.info("Schedule configured: Tue/Sat=post, Thu/Sun=reel, Mon=parse")
    return scheduler


async def _scheduled_generate(post_type: str):
    """Scheduled content generation job."""
    log.info("Scheduled generation triggered: %s", post_type)
    
    try:
        from pipeline import generate_and_queue_post
        result = await generate_and_queue_post(post_type=post_type)
        
        if result and "error" not in result:
            # Send for approval via TG
            if _bot_app and _bot_app.bot:
                from bot.handlers import send_for_approval
                await send_for_approval(
                    bot=_bot_app.bot,
                    queue_id=result["queue_id"],
                    post_type=post_type,
                    topic=result["topic"],
                    text=result["text"],
                    cover_path=result.get("cover_path"),
                    video_path=result.get("video_path"),
                )
            log.info("Content #%d generated and sent for approval", result["queue_id"])
        else:
            error = result.get("error", "Unknown") if result else "Generation failed"
            log.error("Scheduled generation failed: %s", error)
            
            # Notify owner about failure with detailed error analysis
            if _bot_app and _bot_app.bot:
                try:
                    from bot.handlers import notify_owner_error, _classify_error
                    error_detail = _classify_error(error)
                    await notify_owner_error(
                        _bot_app.bot,
                        f"Плановая генерация {post_type}",
                        error_detail,
                    )
                except Exception:
                    pass
    except Exception as e:
        log.error("Scheduled generation exception: %s", e, exc_info=True)
        # Notify owner about unexpected exception
        if _bot_app and _bot_app.bot:
            try:
                from bot.handlers import notify_owner_error, _classify_error
                error_detail = _classify_error(str(e))
                await notify_owner_error(
                    _bot_app.bot,
                    f"Плановая генерация {post_type} (исключение)",
                    error_detail,
                )
            except Exception:
                pass


async def _scheduled_parse_competitors():
    """Weekly competitor parsing job."""
    log.info("Scheduled competitor parsing triggered")
    
    try:
        from content.parser import parse_all_competitors, generate_topics_from_parsed
        
        results = await parse_all_competitors()
        log.info("Parsed competitors: %s", results)
        
        # Generate AI topics from parsed content
        new_topics = await generate_topics_from_parsed(count=10)
        log.info("Generated %d new topics from parsed content", len(new_topics))
        
        # Notify owner
        if _bot_app and _bot_app.bot:
            from config import TG_OWNER_CHAT_ID
            total_parsed = sum(results.values())
            try:
                await _bot_app.bot.send_message(
                    chat_id=TG_OWNER_CHAT_ID,
                    text=(
                        f"📊 Еженедельный парсинг конкурентов:\n"
                        f"Спарсено тем: {total_parsed}\n"
                        f"Сгенерировано новых тем: {len(new_topics)}"
                    ),
                )
            except Exception:
                pass
    except Exception as e:
        log.error("Competitor parsing exception: %s", e, exc_info=True)
        if _bot_app and _bot_app.bot:
            try:
                from bot.handlers import notify_owner_error
                await notify_owner_error(
                    _bot_app.bot,
                    "Парсинг конкурентов",
                    str(e),
                )
            except Exception:
                pass


# Schedule config: maps job_id -> (day_of_week, gen_hour, publish_hour, post_type, label)
SCHEDULE_CONFIG = {
    "gen_tue_post": {"day": "tue", "gen_hour": 17, "pub_hour": 19, "type": "post", "label": "Вторник пост"},
    "gen_thu_reel": {"day": "thu", "gen_hour": 17, "pub_hour": 19, "type": "reel", "label": "Четверг рилс"},
    "gen_sat_post": {"day": "sat", "gen_hour": 8, "pub_hour": 10, "type": "post", "label": "Суббота пост"},
    "gen_sun_reel": {"day": "sun", "gen_hour": 8, "pub_hour": 10, "type": "reel", "label": "Воскресенье рилс"},
    "parse_competitors": {"day": "mon", "gen_hour": 6, "pub_hour": None, "type": "parse", "label": "Понедельник парсинг"},
}

DAY_NAMES_RU = {
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
    "fri": "Пт", "sat": "Сб", "sun": "Вс",
}

DAY_FULL_RU = {
    "mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг",
    "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье",
}


def list_scheduled_jobs() -> list[dict]:
    """List all scheduled jobs with config info."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        cfg = SCHEDULE_CONFIG.get(job.id, {})
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "—",
            "trigger": str(job.trigger),
            "day": cfg.get("day", ""),
            "gen_hour": cfg.get("gen_hour"),
            "pub_hour": cfg.get("pub_hour"),
            "type": cfg.get("type", ""),
            "label": cfg.get("label", job.name),
        })
    return jobs


def reschedule_job(job_id: str, new_day: str, new_hour: int) -> bool:
    """Reschedule a job to a new day and hour.
    
    Args:
        job_id: The scheduler job ID (e.g. 'gen_tue_post')
        new_day: Day of week (mon/tue/wed/thu/fri/sat/sun)
        new_hour: Publication hour (generation will be 2 hours before)
    
    Returns:
        True if rescheduled successfully
    """
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        return False
    
    cfg = SCHEDULE_CONFIG.get(job_id)
    if not cfg:
        return False
    
    # For content jobs, generation is 2h before publish
    if cfg["type"] in ("post", "reel"):
        gen_hour = max(0, new_hour - 2)
    else:
        gen_hour = new_hour
    
    job.reschedule(
        CronTrigger(day_of_week=new_day, hour=gen_hour, minute=0, timezone=TIMEZONE)
    )
    
    # Update config
    cfg["day"] = new_day
    cfg["gen_hour"] = gen_hour
    cfg["pub_hour"] = new_hour if cfg["type"] in ("post", "reel") else None
    cfg["label"] = f"{DAY_FULL_RU.get(new_day, new_day)} {cfg['type']}"
    
    log.info("Rescheduled %s to %s at %02d:00 (gen %02d:00)", job_id, new_day, new_hour, gen_hour)
    return True
