"""Telegram bot handlers for content approval and media upload."""
from __future__ import annotations

import logging
import os
import shutil
import time
import traceback
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

from config import TG_BOT_TOKEN, TG_OWNER_CHAT_ID, SOURCE_PHOTOS, SOURCE_VIDEOS, GENERATED_DIR
from db import get_queue_item, execute_insert, execute

log = logging.getLogger(__name__)

# Callback data prefixes
APPROVE_PREFIX = "approve_"
REJECT_PREFIX = "reject_"
REGENERATE_PREFIX = "regen_"

# Schedule editing prefixes
SCHED_EDIT_PREFIX = "sched_edit_"
SCHED_DAY_PREFIX = "sched_day_"
SCHED_HOUR_PREFIX = "sched_hour_"

# Video management prefixes
VIDEO_DELETE_PREFIX = "vdel_"
VIDEO_CONFIRM_DEL_PREFIX = "vdelok_"
VIDEO_CANCEL_DEL_PREFIX = "vdelno_"

# Persistent menu keyboard
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("\U0001f4dd \u041f\u043e\u0441\u0442"), KeyboardButton("\U0001f3ac \u0420\u0438\u043b\u0441")],
        [KeyboardButton("\U0001f4c5 \u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435"), KeyboardButton("\U0001f4ca \u0421\u0442\u0430\u0442\u0443\u0441")],
        [KeyboardButton("\U0001f3a5 \u0412\u0438\u0434\u0435\u043e"), KeyboardButton("\u2753 \u041f\u043e\u043c\u043e\u0449\u044c")],
    ],
    resize_keyboard=True,
)


def build_bot_app() -> Application:
    """Build and configure the Telegram bot application."""
    app = Application.builder().token(TG_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("topics", cmd_topics))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("videos", cmd_videos))

    # Callback query handler (approve/reject/schedule/video buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Text-button handler (persistent menu buttons)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text_button,
    ))

    # Media upload handler (photos and videos from owner)
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL,
        handle_media_upload,
    ))

    return app


def _is_owner(user_id: int) -> bool:
    """Check if the user is the bot owner."""
    return user_id == TG_OWNER_CHAT_ID


async def notify_owner_error(bot, error_context: str, error_detail: str):
    """Send error notification to the bot owner via Telegram."""
    try:
        detail = error_detail[:1500] if len(error_detail) > 1500 else error_detail
        await bot.send_message(
            chat_id=TG_OWNER_CHAT_ID,
            text=(
                "\u26a0\ufe0f *\u041e\u0448\u0438\u0431\u043a\u0430 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438*\n\n"
                f"\U0001f4cb {error_context}\n\n"
                f"\u274c *\u041f\u0440\u0438\u0447\u0438\u043d\u0430:*\n`{detail}`\n\n"
                "\U0001f504 \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 /generate \u0434\u043b\u044f \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0439 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("Failed to send error notification: %s", e)


def _classify_error(error_text: str) -> str:
    """Classify error into a user-friendly description with cause analysis."""
    err = error_text.lower()
    if "runpod" in err or "lip-sync" in err or "lipsync" in err:
        if "balance" in err or "insufficient" in err or "credits" in err:
            return "\U0001f4b0 RunPod: \u043d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0441\u0440\u0435\u0434\u0441\u0442\u0432. \u041f\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435 RunPod."
        if "timeout" in err or "timed out" in err:
            return "\u23f0 RunPod: \u0442\u0430\u0439\u043c\u0430\u0443\u0442 lip-sync (>30 \u043c\u0438\u043d). GPU \u043f\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043d."
        if "failed" in err:
            return "\U0001f6a8 RunPod: \u043e\u0448\u0438\u0431\u043a\u0430 lip-sync. \u041f\u0440\u043e\u0431\u043b\u0435\u043c\u0430 GPU/\u043c\u043e\u0434\u0435\u043b\u044c."
        return f"\U0001f916 RunPod (lip-sync): {error_text}"
    if "elevenlabs" in err or "tts" in err:
        if "quota" in err or "limit" in err or "exceeded" in err:
            return "\U0001f50a ElevenLabs: \u043b\u0438\u043c\u0438\u0442 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432 \u0438\u0441\u0447\u0435\u0440\u043f\u0430\u043d."
        if "key" in err or "auth" in err or "permission" in err:
            return "\U0001f511 ElevenLabs: \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0430 \u0441 API-\u043a\u043b\u044e\u0447\u043e\u043c."
        return f"\U0001f3a4 ElevenLabs (TTS): {error_text}"
    if "openrouter" in err or "gemini" in err:
        if "rate" in err or "limit" in err:
            return "\U0001f9e0 AI: \u043f\u0440\u0435\u0432\u044b\u0448\u0435\u043d \u043b\u0438\u043c\u0438\u0442 OpenRouter."
        if "key" in err or "auth" in err:
            return "\U0001f511 OpenRouter: \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0430 \u0441 API-\u043a\u043b\u044e\u0447\u043e\u043c."
        return f"\U0001f9e0 AI (OpenRouter): {error_text}"
    if "vk" in err:
        if "token" in err or "auth" in err:
            return "\U0001f511 VK: \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0430 \u0441 \u0442\u043e\u043a\u0435\u043d\u043e\u043c."
        return f"\U0001f4e2 VK API: {error_text}"
    if "ffmpeg" in err:
        return f"\U0001f3ac FFmpeg: {error_text}"
    if "no source videos" in err:
        return "\U0001f3a5 \u041d\u0435\u0442 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0445 \u0432\u0438\u0434\u0435\u043e. \u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u0435 \u0447\u0435\u0440\u0435\u0437 \u0431\u043e\u0442\u0430."
    return f"\u2753 {error_text}"


# ── Basic Commands ───────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("\u26d4 \u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043f\u0440\u0435\u0449\u0451\u043d.")
        return

    await update.message.reply_text(
        "\U0001f3e0 *\u041f\u0440\u0430\u0432\u044b\u0439 \u0411\u0435\u0440\u0435\u0433 \u2014 \u0411\u043e\u0442 \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u043a\u043e\u043d\u0442\u0435\u043d\u0442\u043e\u043c*\n\n"
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 \u043c\u0435\u043d\u044e \u0432\u043d\u0438\u0437\u0443 \u0438\u043b\u0438 \u043a\u043e\u043c\u0430\u043d\u0434\u044b:\n"
        "/generate \u2014 \u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043a\u043e\u043d\u0442\u0435\u043d\u0442\n"
        "/schedule \u2014 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0439\n"
        "/videos \u2014 \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0432\u0438\u0434\u0435\u043e\n"
        "/status \u2014 \u0441\u0442\u0430\u0442\u0443\u0441 \u0441\u0438\u0441\u0442\u0435\u043c\u044b\n"
        "/help \u2014 \u043f\u043e\u043c\u043e\u0449\u044c\n\n"
        "\U0001f4f8 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0444\u043e\u0442\u043e/\u0432\u0438\u0434\u0435\u043e \u2014 \u043e\u043d\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u044f\u0442\u0441\u044f \u043a\u0430\u043a \u0438\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u0438.",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not _is_owner(update.effective_user.id):
        return

    await update.message.reply_text(
        "\U0001f4cb *\u041a\u0430\u043a \u044d\u0442\u043e \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442:*\n\n"
        "1\ufe0f\u20e3 \u0421\u0438\u0441\u0442\u0435\u043c\u0430 \u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0435\u0442 \u043f\u043e\u0441\u0442/\u0440\u0438\u043b\u0441 \u043f\u043e \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044e\n"
        "2\ufe0f\u20e3 \u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0435\u0442 \u0432\u0430\u043c \u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443 \u0441\u044e\u0434\u0430 \u0432 TG\n"
        "3\ufe0f\u20e3 \u0412\u044b \u043d\u0430\u0436\u0438\u043c\u0430\u0435\u0442\u0435 \u2705 \u041e\u0434\u043e\u0431\u0440\u0438\u0442\u044c \u0438\u043b\u0438 \u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c\n"
        "4\ufe0f\u20e3 \u041f\u0440\u0438 \u043e\u0434\u043e\u0431\u0440\u0435\u043d\u0438\u0438 \u2014 \u043f\u0443\u0431\u043b\u0438\u043a\u0443\u0435\u0442\u0441\u044f \u0432 VK \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438\n"
        "5\ufe0f\u20e3 \u041f\u0440\u0438 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u0438 \u2014 \u043c\u043e\u0436\u043d\u043e \u043f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c\n\n"
        "\U0001f3a5 *\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0432\u0438\u0434\u0435\u043e:*\n"
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0432\u0438\u0434\u0435\u043e \u0432 \u0447\u0430\u0442 \u2014 \u043e\u043d\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f \u043d\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435.\n"
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\U0001f3a5 \u0412\u0438\u0434\u0435\u043e\u00bb \u0447\u0442\u043e\u0431\u044b \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c/\u0443\u0434\u0430\u043b\u0438\u0442\u044c.\n\n"
        "\U0001f4c5 *\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435:*\n"
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\U0001f4c5 \u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u00bb \u0434\u043b\u044f \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0430 \u0438 \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f.",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    if not _is_owner(update.effective_user.id):
        return

    photos = list(SOURCE_PHOTOS.glob("*.png")) + list(SOURCE_PHOTOS.glob("*.jpg")) + list(SOURCE_PHOTOS.glob("*.jpeg"))
    videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))

    pending = execute("SELECT COUNT(*) as cnt FROM content_queue WHERE status='pending_approval'")
    approved = execute("SELECT COUNT(*) as cnt FROM content_queue WHERE status='approved'")
    published = execute("SELECT COUNT(*) as cnt FROM published_posts")

    pending_cnt = pending[0]["cnt"] if pending else 0
    approved_cnt = approved[0]["cnt"] if approved else 0
    published_cnt = published[0]["cnt"] if published else 0

    await update.message.reply_text(
        "\U0001f4ca *\u0421\u0442\u0430\u0442\u0443\u0441 \u0441\u0438\u0441\u0442\u0435\u043c\u044b:*\n\n"
        f"\U0001f4f8 \u0424\u043e\u0442\u043e-\u0438\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u043e\u0432: {len(photos)}\n"
        f"\U0001f3a5 \u0412\u0438\u0434\u0435\u043e-\u0438\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u043e\u0432: {len(videos)}\n\n"
        f"\u23f3 \u041e\u0436\u0438\u0434\u0430\u044e\u0442 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438: {pending_cnt}\n"
        f"\u2705 \u041e\u0434\u043e\u0431\u0440\u0435\u043d\u044b (\u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438): {approved_cnt}\n"
        f"\U0001f4e4 \u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d\u043e \u0432\u0441\u0435\u0433\u043e: {published_cnt}",
        parse_mode="Markdown",
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /queue command - show pending items."""
    if not _is_owner(update.effective_user.id):
        return

    rows = execute(
        "SELECT id, post_type, topic, status, scheduled_time FROM content_queue "
        "WHERE status IN ('pending_approval', 'approved') ORDER BY id DESC LIMIT 10"
    )

    if not rows:
        await update.message.reply_text("\U0001f4ed \u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0443\u0441\u0442\u0430.")
        return

    lines = ["\U0001f4cb *\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043a\u043e\u043d\u0442\u0435\u043d\u0442\u0430:*\n"]
    for row in rows:
        status_emoji = "\u23f3" if row["status"] == "pending_approval" else "\u2705"
        sched = row["scheduled_time"] or "\u2014"
        lines.append(f"{status_emoji} #{row['id']} [{row['post_type']}] {row['topic'][:40]}\n   \U0001f4c5 {sched}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    if not _is_owner(update.effective_user.id):
        return

    from content.topics import get_topic_stats
    stats = get_topic_stats()

    published = execute("SELECT COUNT(*) as cnt FROM published_posts")
    pub_cnt = published[0]["cnt"] if published else 0

    lines = [
        "\U0001f4c8 *\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430:*\n",
        f"\U0001f4dd \u0422\u0435\u043c \u0432 \u0431\u0430\u043d\u043a\u0435: {stats['total']}",
        f"\u267b\ufe0f \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u043e \u0442\u0435\u043c: {stats['used']}",
        f"\U0001f4e4 \u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d\u043e \u043f\u043e\u0441\u0442\u043e\u0432: {pub_cnt}\n",
        "*\u041f\u043e \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f\u043c:*",
    ]
    for cat, cnt in stats.get("by_category", {}).items():
        lines.append(f"  \u2022 {cat}: {cnt}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /generate command - manually trigger content generation."""
    if not _is_owner(update.effective_user.id):
        return

    args = context.args
    if args and args[0].lower() in ("post", "reel"):
        types_to_generate = [args[0].lower()]
    else:
        types_to_generate = ["post", "reel"]

    for post_type in types_to_generate:
        type_label = "\U0001f4dd \u043f\u043e\u0441\u0442" if post_type == "post" else "\U0001f3ac \u0440\u0438\u043b\u0441"
        msg = await update.message.reply_text(f"\u23f3 \u0413\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e {type_label}...")

        try:
            from pipeline import generate_and_queue_post
            result = await generate_and_queue_post(post_type=post_type)

            if result and "error" not in result:
                queue_id = result["queue_id"]
                await msg.edit_text(f"\u2705 {type_label.capitalize()} #{queue_id} \u0441\u043e\u0437\u0434\u0430\u043d. \u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u044e \u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443...")

                await send_for_approval(
                    bot=context.bot,
                    queue_id=queue_id,
                    post_type=post_type,
                    topic=result["topic"],
                    text=result["text"],
                    cover_path=result.get("cover_path"),
                    video_path=result.get("video_path"),
                )
            else:
                error = result.get("error", "Unknown error") if result else "Generation failed"
                await msg.edit_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 {type_label}: {error}")
        except Exception as e:
            log.error("Manual generation of %s failed: %s", post_type, e, exc_info=True)
            error_detail = _classify_error(str(e))
            await msg.edit_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 {type_label}:\n{error_detail}")


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /topics command - show topic bank info."""
    if not _is_owner(update.effective_user.id):
        return

    from content.topics import get_topic_stats
    stats = get_topic_stats()

    rows = execute(
        "SELECT topic, category FROM topics_bank ORDER BY used_count ASC, last_used_at ASC NULLS FIRST LIMIT 3"
    )

    lines = [f"\U0001f5c2 *\u0411\u0430\u043d\u043a \u0442\u0435\u043c ({stats['total']} \u0442\u0435\u043c):*\n"]
    lines.append("*\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0435 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438:*")
    for row in rows:
        lines.append(f"  \u2022 [{row['category']}] {row['topic'][:50]}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Schedule ─────────────────────────────────────────────────────────

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule command - show publishing schedule with edit buttons."""
    if not _is_owner(update.effective_user.id):
        return

    await _send_schedule_message(update.message)


async def _send_schedule_message(message):
    """Build and send the pretty schedule message with inline edit buttons."""
    from scheduler import list_scheduled_jobs, SCHEDULE_CONFIG, DAY_NAMES_RU

    jobs = list_scheduled_jobs()

    type_icons = {"post": "\U0001f4dd", "reel": "\U0001f3ac", "parse": "\U0001f50d"}
    type_labels_ru = {"post": "\u041f\u043e\u0441\u0442", "reel": "\u0420\u0438\u043b\u0441", "parse": "\u041f\u0430\u0440\u0441\u0438\u043d\u0433"}

    lines = [
        "\U0001f4c5 *\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0439*",
        "\u2501" * 24,
        "",
    ]

    # Sort by day order
    day_order = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    sorted_items = sorted(SCHEDULE_CONFIG.items(), key=lambda x: day_order.get(x[1]["day"], 99))

    for job_id, cfg in sorted_items:
        day_short = DAY_NAMES_RU.get(cfg["day"], cfg["day"])
        icon = type_icons.get(cfg["type"], "\u2753")
        label = type_labels_ru.get(cfg["type"], cfg["type"])
        if cfg["pub_hour"] is not None:
            time_str = f"{cfg['pub_hour']:02d}:00"
            gen_str = f"(\u0433\u0435\u043d. {cfg['gen_hour']:02d}:00)"
        else:
            time_str = f"{cfg['gen_hour']:02d}:00"
            gen_str = ""
        line = f"{icon}  *{day_short}*  {time_str} \u2014 {label}"
        if gen_str:
            line += f"  _{gen_str}_"
        lines.append(line)

    lines.append("")
    lines.append("\u23f0 _\u0427\u0430\u0441\u043e\u0432\u043e\u0439 \u043f\u043e\u044f\u0441: MSK (\u041c\u043e\u0441\u043a\u0432\u0430)_")

    # Next runs
    if jobs:
        lines.append("")
        lines.append("\u2501" * 24)
        lines.append("\U0001f552 *\u0411\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0435 \u0437\u0430\u043f\u0443\u0441\u043a\u0438:*")
        sorted_jobs = sorted(
            [j for j in jobs if j["next_run"] != "\u2014"],
            key=lambda j: j["next_run"],
        )[:3]
        for job in sorted_jobs:
            try:
                from datetime import datetime as dt
                nr = dt.fromisoformat(job["next_run"].split("+")[0].split(".")[0])
                nr_str = nr.strftime("%d.%m %H:%M")
            except Exception:
                nr_str = job["next_run"][:16]
            icon = type_icons.get(job.get("type", ""), "\u23f0")
            lines.append(f"  {icon} {nr_str} \u2014 {job['label']}")

    lines.append("")
    lines.append("\U0001f4a1 _\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435 \u0434\u043b\u044f \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f_")

    text = "\n".join(lines)

    # Build inline buttons for editing each schedule item
    buttons = []
    for job_id, cfg in sorted_items:
        day_short = DAY_NAMES_RU.get(cfg["day"], cfg["day"])
        icon = type_icons.get(cfg["type"], "")
        hour = cfg["pub_hour"] if cfg["pub_hour"] is not None else cfg["gen_hour"]
        btn_text = f"\u270f {icon} {day_short} {hour:02d}:00"
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"{SCHED_EDIT_PREFIX}{job_id}"))

    # Arrange buttons in rows of 2
    keyboard_rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    await message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ── Video Management ─────────────────────────────────────────────────

async def cmd_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /videos command or 'Видео' button - list source videos."""
    if not _is_owner(update.effective_user.id):
        return

    SOURCE_VIDEOS.mkdir(parents=True, exist_ok=True)
    video_files = sorted(
        [f for f in SOURCE_VIDEOS.iterdir() if f.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not video_files:
        await update.message.reply_text(
            "\U0001f3a5 *\u0412\u0438\u0434\u0435\u043e \u043d\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435:*\n\n"
            "\u274c \u041d\u0435\u0442 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d\u043d\u044b\u0445 \u0432\u0438\u0434\u0435\u043e.\n\n"
            "\U0001f4e4 \u0427\u0442\u043e\u0431\u044b \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u2014 \u043f\u0440\u043e\u0441\u0442\u043e \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0432\u0438\u0434\u0435\u043e \u0432 \u044d\u0442\u043e\u0442 \u0447\u0430\u0442.",
            parse_mode="Markdown",
            reply_markup=MAIN_MENU,
        )
        return

    # Calculate total size
    total_size = sum(f.stat().st_size for f in video_files)
    if total_size < 1024**3:
        size_str = f"{total_size / (1024*1024):.1f} \u041c\u0411"
    else:
        size_str = f"{total_size / (1024**3):.2f} \u0413\u0411"

    lines = [
        f"\U0001f3a5 *\u0412\u0438\u0434\u0435\u043e \u043d\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435* ({len(video_files)} \u0448\u0442., {size_str}):",
        "\u2501" * 24,
        "",
    ]

    buttons = []
    for i, f in enumerate(video_files[:20], 1):
        fsize = f.stat().st_size / (1024 * 1024)
        lines.append(f"{i}. `{f.name}` ({fsize:.1f} \u041c\u0411)")
        short_name = f.name[:40]
        buttons.append([
            InlineKeyboardButton(
                f"\U0001f5d1 {i}. {f.name[:25]}",
                callback_data=f"{VIDEO_DELETE_PREFIX}{short_name}",
            )
        ])

    lines.append("")
    lines.append("\U0001f4e4 _\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0432\u0438\u0434\u0435\u043e \u0432 \u0447\u0430\u0442 \u0434\u043b\u044f \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438_")
    lines.append("\U0001f5d1 _\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f_")

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── Menu Button Router ───────────────────────────────────────────────

async def handle_text_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle persistent menu button presses."""
    if not _is_owner(update.effective_user.id):
        return

    text = update.message.text.strip()

    if "\u041f\u043e\u0441\u0442" in text and "\u0420\u0438\u043b\u0441" not in text:
        context.args = ["post"]
        await cmd_generate(update, context)
    elif "\u0420\u0438\u043b\u0441" in text:
        context.args = ["reel"]
        await cmd_generate(update, context)
    elif "\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435" in text:
        await cmd_schedule(update, context)
    elif "\u0412\u0438\u0434\u0435\u043e" in text:
        await cmd_videos(update, context)
    elif "\u0421\u0442\u0430\u0442\u0443\u0441" in text:
        await cmd_status(update, context)
    elif "\u041f\u043e\u043c\u043e\u0449\u044c" in text:
        await cmd_help(update, context)


# ── Content Approval ─────────────────────────────────────────────────

async def send_for_approval(
    bot,
    queue_id: int,
    post_type: str,
    topic: str,
    text: str,
    cover_path: str | None = None,
    video_path: str | None = None,
):
    """Send content to owner for approval via Telegram."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 \u041e\u0434\u043e\u0431\u0440\u0438\u0442\u044c", callback_data=f"{APPROVE_PREFIX}{queue_id}"),
            InlineKeyboardButton("\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c", callback_data=f"{REJECT_PREFIX}{queue_id}"),
        ],
        [
            InlineKeyboardButton("\U0001f504 \u041f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c", callback_data=f"{REGENERATE_PREFIX}{queue_id}"),
        ],
    ])

    type_label = "\U0001f4dd \u041f\u041e\u0421\u0422" if post_type == "post" else "\U0001f3ac \u0420\u0418\u041b\u0421"

    caption = (
        f"{type_label} #{queue_id}\n"
        f"\U0001f4cc \u0422\u0435\u043c\u0430: {topic}\n\n"
        f"{text[:800]}{'...' if len(text) > 800 else ''}"
    )

    try:
        if cover_path and Path(cover_path).exists():
            with open(cover_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=TG_OWNER_CHAT_ID,
                    photo=photo,
                    caption=caption[:1024],
                    reply_markup=keyboard,
                )
        elif video_path and Path(video_path).exists():
            with open(video_path, "rb") as video:
                await bot.send_video(
                    chat_id=TG_OWNER_CHAT_ID,
                    video=video,
                    caption=caption[:1024],
                    reply_markup=keyboard,
                )
        else:
            await bot.send_message(
                chat_id=TG_OWNER_CHAT_ID,
                text=caption[:4096],
                reply_markup=keyboard,
            )
        log.info("Content #%d sent for approval", queue_id)
    except Exception as e:
        log.error("Failed to send content for approval: %s", e)


# ── Callback Handler ─────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks."""
    query = update.callback_query
    await query.answer()

    if not _is_owner(query.from_user.id):
        return

    data = query.data

    # Content approval callbacks
    if data.startswith(APPROVE_PREFIX):
        queue_id = int(data[len(APPROVE_PREFIX):])
        await _handle_approve(query, queue_id, context)
    elif data.startswith(REJECT_PREFIX):
        queue_id = int(data[len(REJECT_PREFIX):])
        await _handle_reject(query, queue_id)
    elif data.startswith(REGENERATE_PREFIX):
        queue_id = int(data[len(REGENERATE_PREFIX):])
        await _handle_regenerate(query, queue_id, context)

    # Schedule editing callbacks
    elif data.startswith(SCHED_EDIT_PREFIX):
        job_id = data[len(SCHED_EDIT_PREFIX):]
        await _handle_schedule_edit(query, job_id)
    elif data.startswith(SCHED_DAY_PREFIX):
        parts = data[len(SCHED_DAY_PREFIX):].rsplit("_", 1)
        if len(parts) == 2:
            await _handle_schedule_day(query, parts[0], parts[1])
    elif data.startswith(SCHED_HOUR_PREFIX):
        parts = data[len(SCHED_HOUR_PREFIX):].rsplit("_", 2)
        if len(parts) == 3:
            await _handle_schedule_hour(query, parts[0], parts[1], int(parts[2]))

    # Video management callbacks
    elif data.startswith(VIDEO_DELETE_PREFIX):
        filename = data[len(VIDEO_DELETE_PREFIX):]
        await _handle_video_delete_confirm(query, filename)
    elif data.startswith(VIDEO_CONFIRM_DEL_PREFIX):
        filename = data[len(VIDEO_CONFIRM_DEL_PREFIX):]
        await _handle_video_delete(query, filename)
    elif data.startswith(VIDEO_CANCEL_DEL_PREFIX):
        await query.edit_message_text("\u274c \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.")


# ── Approve / Reject / Regenerate ────────────────────────────────────

async def _handle_approve(query, queue_id: int, context=None):
    """Approve content for publishing."""
    from datetime import datetime

    item = get_queue_item(queue_id)
    if not item:
        try:
            await query.edit_message_caption(caption="\u274c \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        except Exception:
            await query.edit_message_text(text="\u274c \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        return

    execute_insert(
        "UPDATE content_queue SET status='approved', approved_at=? WHERE id=?",
        (datetime.now().isoformat(), queue_id),
    )

    try:
        await query.edit_message_caption(
            caption=f"\u2705 \u041e\u0434\u043e\u0431\u0440\u0435\u043d\u043e! #{queue_id} \u0431\u0443\u0434\u0435\u0442 \u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d.\n\n{item['topic']}"
        )
    except Exception:
        await query.edit_message_text(
            text=f"\u2705 \u041e\u0434\u043e\u0431\u0440\u0435\u043d\u043e! #{queue_id} \u0431\u0443\u0434\u0435\u0442 \u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d.\n\n{item['topic']}"
        )
    log.info("Content #%d approved", queue_id)

    # Trigger immediate publish
    try:
        from pipeline import publish_approved_item
        result = await publish_approved_item(queue_id)
        if result:
            await query.message.reply_text(f"\U0001f4e4 \u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d\u043e \u0432 VK! Post ID: {result}")
        else:
            await query.message.reply_text("\u23f3 \u0411\u0443\u0434\u0435\u0442 \u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d\u043e \u043f\u043e \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044e.")
    except Exception as e:
        log.error("Immediate publish failed: %s", e, exc_info=True)
        error_detail = _classify_error(str(e))
        await query.message.reply_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0438:\n{error_detail}")


async def _handle_reject(query, queue_id: int):
    """Reject content."""
    execute_insert(
        "UPDATE content_queue SET status='rejected', rejection_reason='manual' WHERE id=?",
        (queue_id,),
    )
    try:
        await query.edit_message_caption(
            caption=f"\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u043e. #{queue_id}\n\u041c\u043e\u0436\u0435\u0442\u0435 \u043d\u0430\u0436\u0430\u0442\u044c \U0001f504 \u0434\u043b\u044f \u043f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438."
        )
    except Exception:
        await query.edit_message_text(
            text=f"\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u043e. #{queue_id}\n\u041c\u043e\u0436\u0435\u0442\u0435 \u043d\u0430\u0436\u0430\u0442\u044c \U0001f504 \u0434\u043b\u044f \u043f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438."
        )
    log.info("Content #%d rejected", queue_id)


async def _handle_regenerate(query, queue_id: int, context):
    """Regenerate content with the same topic and send new content for approval."""
    item = get_queue_item(queue_id)
    if not item:
        try:
            await query.edit_message_caption(caption="\u274c \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        except Exception:
            await query.edit_message_text(text="\u274c \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        return

    try:
        await query.edit_message_caption(caption="\U0001f504 \u041f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f...")
    except Exception:
        await query.edit_message_text(text="\U0001f504 \u041f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f...")

    try:
        from pipeline import generate_and_queue_post
        result = await generate_and_queue_post(
            post_type=item["post_type"],
            force_topic=item["topic"],
        )
        if result and "error" not in result:
            # Mark old as replaced
            execute_insert(
                "UPDATE content_queue SET status='replaced' WHERE id=?",
                (queue_id,),
            )
            # Send the NEW content for approval (with image/video + buttons)
            await send_for_approval(
                bot=context.bot,
                queue_id=result["queue_id"],
                post_type=item["post_type"],
                topic=result["topic"],
                text=result["text"],
                cover_path=result.get("cover_path"),
                video_path=result.get("video_path"),
            )
        else:
            error = result.get("error", "Unknown") if result else "Failed"
            error_detail = _classify_error(error)
            await query.message.reply_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438:\n{error_detail}")
    except Exception as e:
        log.error("Regeneration failed: %s", e, exc_info=True)
        error_detail = _classify_error(str(e))
        await query.message.reply_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0435\u0440\u0435\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438:\n{error_detail}")


# ── Schedule Editing Handlers ────────────────────────────────────────

async def _handle_schedule_edit(query, job_id: str):
    """Show day-of-week buttons for editing a schedule item."""
    from scheduler import SCHEDULE_CONFIG, DAY_FULL_RU

    cfg = SCHEDULE_CONFIG.get(job_id)
    if not cfg:
        await query.edit_message_text("\u274c \u0417\u0430\u0434\u0430\u0447\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        return

    type_labels_ru = {"post": "\u041f\u043e\u0441\u0442", "reel": "\u0420\u0438\u043b\u0441", "parse": "\u041f\u0430\u0440\u0441\u0438\u043d\u0433"}
    label = type_labels_ru.get(cfg["type"], cfg["type"])
    current_day = DAY_FULL_RU.get(cfg["day"], cfg["day"])
    hour = cfg["pub_hour"] if cfg["pub_hour"] is not None else cfg["gen_hour"]

    text = (
        f"\u270f *\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435: {label}*\n\n"
        f"\u0421\u0435\u0439\u0447\u0430\u0441: *{current_day}* \u0432 *{hour:02d}:00* MSK\n\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u043e\u0432\u044b\u0439 \u0434\u0435\u043d\u044c \u043d\u0435\u0434\u0435\u043b\u0438:"
    )

    days = [("\u041f\u043d", "mon"), ("\u0412\u0442", "tue"), ("\u0421\u0440", "wed"), ("\u0427\u0442", "thu"),
            ("\u041f\u0442", "fri"), ("\u0421\u0431", "sat"), ("\u0412\u0441", "sun")]
    buttons = []
    for day_ru, day_en in days:
        marker = " \u2705" if day_en == cfg["day"] else ""
        buttons.append(InlineKeyboardButton(
            f"{day_ru}{marker}",
            callback_data=f"{SCHED_DAY_PREFIX}{job_id}_{day_en}",
        ))

    keyboard = InlineKeyboardMarkup([
        buttons[:4],
        buttons[4:],
    ])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def _handle_schedule_day(query, job_id: str, new_day: str):
    """Show hour selection after day is chosen."""
    from scheduler import SCHEDULE_CONFIG, DAY_FULL_RU

    cfg = SCHEDULE_CONFIG.get(job_id)
    if not cfg:
        await query.edit_message_text("\u274c \u0417\u0430\u0434\u0430\u0447\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        return

    day_name = DAY_FULL_RU.get(new_day, new_day)
    type_labels_ru = {"post": "\u041f\u043e\u0441\u0442", "reel": "\u0420\u0438\u043b\u0441", "parse": "\u041f\u0430\u0440\u0441\u0438\u043d\u0433"}
    label = type_labels_ru.get(cfg["type"], cfg["type"])
    current_hour = cfg["pub_hour"] if cfg["pub_hour"] is not None else cfg["gen_hour"]

    text = (
        f"\u270f *\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435: {label}*\n\n"
        f"\u0414\u0435\u043d\u044c: *{day_name}*\n\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0440\u0435\u043c\u044f \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0438 (MSK):"
    )

    hours = [6, 8, 9, 10, 12, 14, 16, 17, 18, 19, 20, 21]
    buttons = []
    for h in hours:
        marker = " \u2705" if h == current_hour else ""
        buttons.append(InlineKeyboardButton(
            f"{h:02d}:00{marker}",
            callback_data=f"{SCHED_HOUR_PREFIX}{job_id}_{new_day}_{h}",
        ))

    keyboard = InlineKeyboardMarkup([
        buttons[:4],
        buttons[4:8],
        buttons[8:],
    ])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def _handle_schedule_hour(query, job_id: str, new_day: str, new_hour: int):
    """Apply the schedule change."""
    from scheduler import reschedule_job, SCHEDULE_CONFIG, DAY_FULL_RU

    success = reschedule_job(job_id, new_day, new_hour)
    if not success:
        await query.edit_message_text("\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435")
        return

    day_name = DAY_FULL_RU.get(new_day, new_day)
    cfg = SCHEDULE_CONFIG.get(job_id, {})
    type_labels_ru = {"post": "\u041f\u043e\u0441\u0442", "reel": "\u0420\u0438\u043b\u0441", "parse": "\u041f\u0430\u0440\u0441\u0438\u043d\u0433"}
    label = type_labels_ru.get(cfg.get("type", ""), "")

    gen_hour = max(0, new_hour - 2) if cfg.get("type") in ("post", "reel") else new_hour

    await query.edit_message_text(
        f"\u2705 *\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e!*\n\n"
        f"{label}: *{day_name}* \u0432 *{new_hour:02d}:00* MSK\n"
        f"_(\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f \u0432 {gen_hour:02d}:00)_\n\n"
        f"\u041d\u0430\u0436\u043c\u0438\u0442\u0435 /schedule \u0447\u0442\u043e\u0431\u044b \u0443\u0432\u0438\u0434\u0435\u0442\u044c \u043f\u043e\u043b\u043d\u043e\u0435 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435.",
        parse_mode="Markdown",
    )


# ── Video Delete Handlers ────────────────────────────────────────────

async def _handle_video_delete_confirm(query, filename: str):
    """Ask for confirmation before deleting a video."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 \u0414\u0430, \u0443\u0434\u0430\u043b\u0438\u0442\u044c", callback_data=f"{VIDEO_CONFIRM_DEL_PREFIX}{filename}"),
            InlineKeyboardButton("\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data=f"{VIDEO_CANCEL_DEL_PREFIX}"),
        ]
    ])
    await query.edit_message_text(
        f"\U0001f5d1 \u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0432\u0438\u0434\u0435\u043e `{filename}`?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def _handle_video_delete(query, filename: str):
    """Actually delete a video file."""
    filepath = SOURCE_VIDEOS / filename
    if filepath.exists() and filepath.is_file():
        filepath.unlink()
        remaining = len([f for f in SOURCE_VIDEOS.iterdir() if f.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv")])
        await query.edit_message_text(
            f"\u2705 \u0412\u0438\u0434\u0435\u043e `{filename}` \u0443\u0434\u0430\u043b\u0435\u043d\u043e.\n"
            f"\U0001f3a5 \u041e\u0441\u0442\u0430\u043b\u043e\u0441\u044c \u0432\u0438\u0434\u0435\u043e: {remaining}\n\n"
            "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 /videos \u0434\u043b\u044f \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0430 \u0441\u043f\u0438\u0441\u043a\u0430.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(f"\u274c \u0424\u0430\u0439\u043b `{filename}` \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", parse_mode="Markdown")


# ── Media Upload Handler ─────────────────────────────────────────────

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo/video uploads from owner - save as source media."""
    if not _is_owner(update.effective_user.id):
        return

    message = update.message
    saved_path = None

    try:
        if message.photo:
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)

            SOURCE_PHOTOS.mkdir(parents=True, exist_ok=True)
            filename = f"tg_photo_{int(time.time())}_{photo.file_id[-8:]}.jpg"
            saved_path = SOURCE_PHOTOS / filename
            await file.download_to_drive(str(saved_path))

            await message.reply_text(f"\U0001f4f8 \u0424\u043e\u0442\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e: {filename}\n\U0001f4c2 \u0412\u0441\u0435\u0433\u043e \u0444\u043e\u0442\u043e: {len(list(SOURCE_PHOTOS.glob('*')))}")

        elif message.video:
            video = message.video
            file = await context.bot.get_file(video.file_id)

            SOURCE_VIDEOS.mkdir(parents=True, exist_ok=True)
            ext = Path(video.file_name).suffix if video.file_name else ".mp4"
            filename = f"tg_video_{int(time.time())}_{video.file_id[-8:]}{ext}"
            saved_path = SOURCE_VIDEOS / filename
            await file.download_to_drive(str(saved_path))

            await message.reply_text(
                f"\U0001f3a5 \u0412\u0438\u0434\u0435\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e: {filename}\n"
                f"\U0001f4c2 \u0412\u0441\u0435\u0433\u043e \u0432\u0438\u0434\u0435\u043e: {len(list(SOURCE_VIDEOS.glob('*')))}\n\n"
                "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 /videos \u0434\u043b\u044f \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0430 \u0441\u043f\u0438\u0441\u043a\u0430.",
            )

        elif message.document:
            doc = message.document
            file = await context.bot.get_file(doc.file_id)

            if doc.mime_type and doc.mime_type.startswith("image/"):
                SOURCE_PHOTOS.mkdir(parents=True, exist_ok=True)
                filename = doc.file_name or f"doc_photo_{int(time.time())}.jpg"
                saved_path = SOURCE_PHOTOS / filename
                await file.download_to_drive(str(saved_path))
                await message.reply_text(f"\U0001f4f8 \u0424\u043e\u0442\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e: {filename}")
            elif doc.mime_type and doc.mime_type.startswith("video/"):
                SOURCE_VIDEOS.mkdir(parents=True, exist_ok=True)
                filename = doc.file_name or f"doc_video_{int(time.time())}.mp4"
                saved_path = SOURCE_VIDEOS / filename
                await file.download_to_drive(str(saved_path))
                await message.reply_text(
                    f"\U0001f3a5 \u0412\u0438\u0434\u0435\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e: {filename}\n"
                    f"\U0001f4c2 \u0412\u0441\u0435\u0433\u043e \u0432\u0438\u0434\u0435\u043e: {len(list(SOURCE_VIDEOS.glob('*')))}\n\n"
                    "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 /videos \u0434\u043b\u044f \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0430 \u0441\u043f\u0438\u0441\u043a\u0430.",
                )
            else:
                await message.reply_text("\u26a0\ufe0f \u041d\u0435\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0444\u043e\u0442\u043e \u0438\u043b\u0438 \u0432\u0438\u0434\u0435\u043e.")

    except Exception as e:
        log.error("Media upload handling failed: %s", e)
        await message.reply_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f: {e}")
