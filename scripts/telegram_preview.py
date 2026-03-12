#!/usr/bin/env python3
"""
Telegram Preview Bot - sends carousel preview to Telegram for review.
Sends images + caption + QC scores with Approve/Reject inline buttons.

Usage: python3 telegram_preview.py <username> [--chat-id CHAT_ID]

Requires:
  pip3 install python-telegram-bot==20.7
  Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import sys
import asyncio
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_preview(username, chat_id=None):
    """Send carousel preview to Telegram."""
    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.constants import ParseMode
    except ImportError:
        print("[ERROR] python-telegram-bot not installed. Run: pip3 install python-telegram-bot==20.7")
        sys.exit(1)

    if not TELEGRAM_BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        print("[ERROR] TELEGRAM_CHAT_ID not set and --chat-id not provided")
        sys.exit(1)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Find latest post package
    posts_dir = os.path.join(WORKSPACE, "posts")
    if not os.path.exists(posts_dir):
        print("[ERROR] No posts directory found")
        sys.exit(1)

    post_files = sorted(Path(posts_dir).glob("%s_post_*.json" % username), reverse=True)
    if not post_files:
        print("[ERROR] No post packages found for @%s" % username)
        sys.exit(1)

    post_path = post_files[0]  # Latest post
    with open(post_path) as f:
        post = json.load(f)

    print("[Telegram] Sending preview for @%s to chat %s..." % (username, target_chat))

    # Load QC report for per-image scores
    qc_path = os.path.join(WORKSPACE, "qc_reports", username + "_qc.json")
    qc_results = {}
    if os.path.exists(qc_path):
        with open(qc_path) as f:
            qc_data = json.load(f)
        for r in qc_data.get("results", []):
            qc_results[r.get("image", "")] = r

    # 1. Send header message
    carousel_theme = post.get("carousel_theme", "N/A")
    image_count = post.get("image_count", 0)
    qc_avg = post.get("qc_summary", {}).get("average_score", 0)
    qc_passed = post.get("qc_summary", {}).get("passed", 0)
    qc_total = post.get("qc_summary", {}).get("total", 0)

    header = (
        "<b>New Carousel Preview</b>\n"
        "Profile: @%s\n"
        "Theme: %s\n"
        "Images: %d\n"
        "QC: %d/%d passed (avg %.1f/10)\n"
        "Status: %s"
    ) % (username, carousel_theme, image_count, qc_passed, qc_total, qc_avg, post.get("status", "unknown"))

    await bot.send_message(chat_id=target_chat, text=header, parse_mode=ParseMode.HTML)
    print("[Telegram] Sent header")

    # 2. Send each image with QC score
    photos_dir = os.path.join(WORKSPACE, "output", "photos", username)
    for i, img_info in enumerate(post.get("images", [])):
        img_path = os.path.join(photos_dir, img_info.get("filename", ""))
        if not os.path.exists(img_path):
            img_path = img_info.get("path", "")

        if not os.path.exists(img_path):
            print("  [SKIP] Image not found: %s" % img_info.get("filename", ""))
            continue

        # Get QC score for this image
        qc_info = qc_results.get(img_info.get("filename", ""), {})
        scores = qc_info.get("scores", {})
        overall = scores.get("overall", 0)
        issues = qc_info.get("issues", [])
        artifact = qc_info.get("artifact_check", {})

        caption = "Slide %d/%d | QC: %.1f/10" % (i + 1, image_count, overall)
        if scores:
            caption += "\nPrompt: %s | Tech: %s | Comp: %s" % (
                scores.get("prompt_adherence", "?"),
                scores.get("technical_quality", "?"),
                scores.get("composition", "?"),
            )
        if artifact:
            arms = artifact.get("arms_count", 2)
            hands = artifact.get("hands_count", 2)
            if arms != 2 or hands != 2:
                caption += "\nARTIFACT: arms=%d, hands=%d" % (arms, hands)
        if issues:
            caption += "\nIssues: " + "; ".join(issues[:3])

        with open(img_path, "rb") as photo:
            await bot.send_photo(chat_id=target_chat, photo=photo, caption=caption)
        print("  [Telegram] Sent slide %d: %s (QC %.1f)" % (i + 1, img_info.get("filename", ""), overall))

    # 3. Send caption + hashtags
    post_caption = post.get("caption", "")
    hashtags = post.get("hashtags", [])
    caption_msg = (
        "<b>Caption:</b>\n%s\n\n"
        "<b>Hashtags:</b>\n%s"
    ) % (post_caption, " ".join(hashtags))

    await bot.send_message(chat_id=target_chat, text=caption_msg, parse_mode=ParseMode.HTML)
    print("[Telegram] Sent caption + hashtags")

    # 4. Send approve/reject buttons
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data="approve_%s" % username),
            InlineKeyboardButton("Reject", callback_data="reject_%s" % username),
        ],
        [
            InlineKeyboardButton("Edit Caption", callback_data="edit_%s" % username),
            InlineKeyboardButton("Regenerate", callback_data="regen_%s" % username),
        ],
    ])

    await bot.send_message(
        chat_id=target_chat,
        text="<b>Action required:</b> Review the carousel above and choose an action.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    print("[Telegram] Sent action buttons")
    print("[Telegram] Preview sent successfully!")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 telegram_preview.py <username> [--chat-id CHAT_ID]")
        sys.exit(1)

    username = sys.argv[1]
    chat_id = None

    if "--chat-id" in sys.argv:
        idx = sys.argv.index("--chat-id")
        chat_id = sys.argv[idx + 1]

    asyncio.run(send_preview(username, chat_id))


if __name__ == "__main__":
    main()
