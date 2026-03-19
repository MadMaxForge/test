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


# Persistent menu keyboard
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("\U0001f4dd Пост"), KeyboardButton("\U0001f3ac Рилс")],
        [KeyboardButton("\U0001f4c5 Расписание"), KeyboardButton("\U0001f4ca Статус")],
        [KeyboardButton("\u2753 Помощь")],
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

    # Callback query handler (approve/reject buttons)
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
                f"\U0001f4cd {error_context}\n\n"
                f"\u274c *\u041f\u0440\u0438\u0447\u0438\u043d\u0430:*\n\`{detail}\`\n\n"
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    await update.message.reply_text(
        "🏠 *Правый Берег — Бот управления контентом*\n\n"
        "Доступные команды:\n"
        "/status — статус системы\n"
        "/queue — очередь на публикацию\n"
        "/generate — сгенерировать новый пост\n"
        "/topics — статистика по темам\n"
        "/stats — общая статистика\n"
        "/help — помощь\n\n"
        "📸 Отправьте фото/видео — они будут сохранены как исходники для контента.",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not _is_owner(update.effective_user.id):
        return

    await update.message.reply_text(
        "📋 *Как это работает:*\n\n"
        "1️⃣ Система генерирует пост/рилс по расписанию\n"
        "2️⃣ Отправляет вам на проверку сюда в TG\n"
        "3️⃣ Вы нажимаете ✅ Одобрить или ❌ Отклонить\n"
        "4️⃣ При одобрении — публикуется в VK автоматически\n"
        "5️⃣ При отклонении — генерируется новый вариант\n\n"
        "📸 *Загрузка медиа:*\n"
        "Просто отправьте фото или видео в этот чат — "
        "они будут сохранены как исходники для будущих постов.\n\n"
        "🔄 /generate — создать пост вручную прямо сейчас",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    if not _is_owner(update.effective_user.id):
        return

    # Count media files
    photos = list(SOURCE_PHOTOS.glob("*.png")) + list(SOURCE_PHOTOS.glob("*.jpg")) + list(SOURCE_PHOTOS.glob("*.jpeg"))
    videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))

    # Count queue items
    pending = execute("SELECT COUNT(*) as cnt FROM content_queue WHERE status='pending_approval'")
    approved = execute("SELECT COUNT(*) as cnt FROM content_queue WHERE status='approved'")
    published = execute("SELECT COUNT(*) as cnt FROM published_posts")

    pending_cnt = pending[0]["cnt"] if pending else 0
    approved_cnt = approved[0]["cnt"] if approved else 0
    published_cnt = published[0]["cnt"] if published else 0

    await update.message.reply_text(
        "📊 *Статус системы:*\n\n"
        f"📸 Фото-исходников: {len(photos)}\n"
        f"🎥 Видео-исходников: {len(videos)}\n\n"
        f"⏳ Ожидают проверки: {pending_cnt}\n"
        f"✅ Одобрены (в очереди): {approved_cnt}\n"
        f"📤 Опубликовано всего: {published_cnt}",
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
        await update.message.reply_text("📭 Очередь пуста.")
        return

    lines = ["📋 *Очередь контента:*\n"]
    for row in rows:
        status_emoji = "⏳" if row["status"] == "pending_approval" else "✅"
        sched = row["scheduled_time"] or "—"
        lines.append(f"{status_emoji} #{row['id']} [{row['post_type']}] {row['topic'][:40]}\n   📅 {sched}")

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
        "📈 *Статистика:*\n",
        f"📝 Тем в банке: {stats['total']}",
        f"♻️ Использовано тем: {stats['used']}",
        f"📤 Опубликовано постов: {pub_cnt}\n",
        "*По категориям:*",
    ]
    for cat, cnt in stats.get("by_category", {}).items():
        lines.append(f"  • {cat}: {cnt}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /generate command - manually trigger content generation.

    Usage:
        /generate       — generate both a post and a reel
        /generate post  — generate only a post
        /generate reel  — generate only a reel
    """
    if not _is_owner(update.effective_user.id):
        return

    args = context.args
    if args and args[0].lower() in ("post", "reel"):
        types_to_generate = [args[0].lower()]
    else:
        # Default: generate both post and reel
        types_to_generate = ["post", "reel"]

    for post_type in types_to_generate:
        type_label = "📝 пост" if post_type == "post" else "🎬 рилс"
        msg = await update.message.reply_text(f"⏳ Генерирую {type_label}...")

        try:
            from pipeline import generate_and_queue_post
            result = await generate_and_queue_post(post_type=post_type)

            if result and "error" not in result:
                queue_id = result["queue_id"]
                await msg.edit_text(f"✅ {type_label.capitalize()} #{queue_id} создан. Отправляю на проверку...")

                # Send for approval
                from bot.handlers import send_for_approval
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
                await msg.edit_text(f"❌ Ошибка {type_label}: {error}")
        except Exception as e:
            log.error("Manual generation of %s failed: %s", post_type, e, exc_info=True)
            error_detail = _classify_error(str(e))
            await msg.edit_text(f"❌ Ошибка {type_label}:\n{error_detail}")


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /topics command - show topic bank info."""
    if not _is_owner(update.effective_user.id):
        return

    from content.topics import get_topic_stats
    stats = get_topic_stats()

    # Get next topic preview
    from content.topics import get_next_topic
    # Don't actually mark as used - just peek
    rows = execute(
        "SELECT topic, category FROM topics_bank ORDER BY used_count ASC, last_used_at ASC NULLS FIRST LIMIT 3"
    )
    
    lines = [f"🗂 *Банк тем ({stats['total']} тем):*\n"]
    lines.append("*Следующие в очереди:*")
    for row in rows:
        lines.append(f"  • [{row['category']}] {row['topic'][:50]}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule command - show publishing schedule and next runs."""
    if not _is_owner(update.effective_user.id):
        return

    from scheduler import list_scheduled_jobs

    jobs = list_scheduled_jobs()

    lines = ["📅 *Расписание публикаций:*\n"]

    lines.append("*Регулярное (MSK):*")
    lines.append("📝 Вт 19:00 — пост (ген. 17:00)")
    lines.append("🎬 Чт 19:00 — рилс (ген. 17:00)")
    lines.append("📝 Сб 10:00 — пост (ген. 08:00)")
    lines.append("🎬 Вс 10:00 — рилс (ген. 08:00)")
    lines.append("🔍 Пн 06:00 — парсинг конкурентов\n")

    if jobs:
        lines.append("*Ближайшие запуски:*")
        for job in sorted(jobs, key=lambda j: j["next_run"]):
            lines.append(f"  ⏰ {job['name']}: {job['next_run']}")
    else:
        lines.append("⚠️ Планировщик не активен")

    lines.append("\n💡 *Вне расписания:*")
    lines.append("Нажмите \"📝 Пост\" или \"🎬 Рилс\" в меню")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_MENU)


async def handle_text_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle persistent menu button presses."""
    if not _is_owner(update.effective_user.id):
        return

    text = update.message.text.strip()

    if "Пост" in text and "Рилс" not in text:
        context.args = ["post"]
        await cmd_generate(update, context)
    elif "Рилс" in text:
        context.args = ["reel"]
        await cmd_generate(update, context)
    elif "Расписание" in text:
        await cmd_schedule(update, context)
    elif "Статус" in text:
        await cmd_status(update, context)
    elif "Помощь" in text:
        await cmd_help(update, context)


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
            InlineKeyboardButton("✅ Одобрить", callback_data=f"{APPROVE_PREFIX}{queue_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"{REJECT_PREFIX}{queue_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Перегенерировать", callback_data=f"{REGENERATE_PREFIX}{queue_id}"),
        ],
    ])

    type_label = "📝 ПОСТ" if post_type == "post" else "🎬 РИЛС"

    caption = (
        f"{type_label} #{queue_id}\n"
        f"📌 Тема: {topic}\n\n"
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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks (approve/reject/regenerate)."""
    query = update.callback_query
    await query.answer()

    if not _is_owner(query.from_user.id):
        return

    data = query.data

    if data.startswith(APPROVE_PREFIX):
        queue_id = int(data[len(APPROVE_PREFIX):])
        await _handle_approve(query, queue_id, context)

    elif data.startswith(REJECT_PREFIX):
        queue_id = int(data[len(REJECT_PREFIX):])
        await _handle_reject(query, queue_id)

    elif data.startswith(REGENERATE_PREFIX):
        queue_id = int(data[len(REGENERATE_PREFIX):])
        await _handle_regenerate(query, queue_id, context)


async def _handle_approve(query, queue_id: int, context=None):
    """Approve content for publishing."""
    from datetime import datetime

    item = get_queue_item(queue_id)
    if not item:
        try:
            await query.edit_message_caption(caption="❌ Элемент не найден")
        except Exception:
            await query.edit_message_text(text="❌ Элемент не найден")
        return

    execute_insert(
        "UPDATE content_queue SET status='approved', approved_at=? WHERE id=?",
        (datetime.now().isoformat(), queue_id),
    )

    try:
        await query.edit_message_caption(
            caption=f"✅ Одобрено! #{queue_id} будет опубликован.\n\n{item['topic']}"
        )
    except Exception:
        await query.edit_message_text(
            text=f"✅ Одобрено! #{queue_id} будет опубликован.\n\n{item['topic']}"
        )
    log.info("Content #%d approved", queue_id)

    # Trigger immediate publish
    try:
        from pipeline import publish_approved_item
        result = await publish_approved_item(queue_id)
        if result:
            await query.message.reply_text(f"📤 Опубликовано в VK! Post ID: {result}")
        else:
            await query.message.reply_text("⏳ Будет опубликовано по расписанию.")
    except Exception as e:
        log.error("Immediate publish failed: %s", e, exc_info=True)
        error_detail = _classify_error(str(e))
        await query.message.reply_text(f"❌ Ошибка публикации:\n{error_detail}")


async def _handle_reject(query, queue_id: int):
    """Reject content."""
    execute_insert(
        "UPDATE content_queue SET status='rejected', rejection_reason='manual' WHERE id=?",
        (queue_id,),
    )
    try:
        await query.edit_message_caption(
            caption=f"❌ Отклонено. #{queue_id}\nМожете нажать 🔄 для перегенерации."
        )
    except Exception:
        await query.edit_message_text(
            text=f"❌ Отклонено. #{queue_id}\nМожете нажать 🔄 для перегенерации."
        )
    log.info("Content #%d rejected", queue_id)


async def _handle_regenerate(query, queue_id: int, context):
    """Regenerate content with the same topic."""
    item = get_queue_item(queue_id)
    if not item:
        await query.edit_message_caption(caption="❌ Элемент не найден")
        return

    await query.edit_message_caption(caption="🔄 Перегенерация...")

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
            await query.message.reply_text(f"🔄 Новый вариант: #{result['queue_id']}")
        else:
            error = result.get("error", "Unknown") if result else "Failed"
            await query.message.reply_text(f"❌ Ошибка перегенерации: {error}")
    except Exception as e:
        log.error("Regeneration failed: %s", e)
        await query.message.reply_text(f"❌ Ошибка: {e}")


async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo/video uploads from owner - save as source media."""
    if not _is_owner(update.effective_user.id):
        return

    message = update.message
    saved_path = None

    try:
        if message.photo:
            # Get highest resolution photo
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            
            SOURCE_PHOTOS.mkdir(parents=True, exist_ok=True)
            filename = f"tg_photo_{int(time.time())}_{photo.file_id[-8:]}.jpg"
            saved_path = SOURCE_PHOTOS / filename
            await file.download_to_drive(str(saved_path))
            
            await message.reply_text(f"📸 Фото сохранено: {filename}\n📂 Всего фото: {len(list(SOURCE_PHOTOS.glob('*')))}")

        elif message.video:
            video = message.video
            file = await context.bot.get_file(video.file_id)
            
            SOURCE_VIDEOS.mkdir(parents=True, exist_ok=True)
            ext = Path(video.file_name).suffix if video.file_name else ".mp4"
            filename = f"tg_video_{int(time.time())}_{video.file_id[-8:]}{ext}"
            saved_path = SOURCE_VIDEOS / filename
            await file.download_to_drive(str(saved_path))
            
            await message.reply_text(f"🎥 Видео сохранено: {filename}\n📂 Всего видео: {len(list(SOURCE_VIDEOS.glob('*')))}")

        elif message.document:
            doc = message.document
            file = await context.bot.get_file(doc.file_id)
            
            if doc.mime_type and doc.mime_type.startswith("image/"):
                SOURCE_PHOTOS.mkdir(parents=True, exist_ok=True)
                filename = doc.file_name or f"doc_photo_{int(time.time())}.jpg"
                saved_path = SOURCE_PHOTOS / filename
                await file.download_to_drive(str(saved_path))
                await message.reply_text(f"📸 Фото сохранено: {filename}")
            elif doc.mime_type and doc.mime_type.startswith("video/"):
                SOURCE_VIDEOS.mkdir(parents=True, exist_ok=True)
                filename = doc.file_name or f"doc_video_{int(time.time())}.mp4"
                saved_path = SOURCE_VIDEOS / filename
                await file.download_to_drive(str(saved_path))
                await message.reply_text(f"🎥 Видео сохранено: {filename}")
            else:
                await message.reply_text("⚠️ Неподдерживаемый формат. Отправьте фото или видео.")

    except Exception as e:
        log.error("Media upload handling failed: %s", e)
        await message.reply_text(f"❌ Ошибка сохранения: {e}")
