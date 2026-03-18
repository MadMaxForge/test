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
            
            # Notify owner about failure
            if _bot_app and _bot_app.bot:
                from config import TG_OWNER_CHAT_ID
                try:
                    await _bot_app.bot.send_message(
                        chat_id=TG_OWNER_CHAT_ID,
                        text=f"⚠️ Ошибка генерации {post_type}: {error}",
                    )
                except Exception:
                    pass
    except Exception as e:
        log.error("Scheduled generation exception: %s", e, exc_info=True)


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


def list_scheduled_jobs() -> list[dict]:
    """List all scheduled jobs."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "—",
            "trigger": str(job.trigger),
        })
    return jobs
