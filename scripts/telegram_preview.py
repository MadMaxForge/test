#!/usr/bin/env python3
"""
Telegram Preview Bot - sends content preview to Telegram for review.

Supports:
  - Carousel preview (photos + caption + QC scores)
  - Reel approval workflow (Z-Image frame + reference video -> approve -> Kling generation)

Usage:
  python3 telegram_preview.py <username> [--chat-id CHAT_ID]
  python3 telegram_preview.py <username> --reel --frame /path/frame.png --ref-video /path/ref.mp4
  python3 telegram_preview.py --listen   # Start polling for approval callbacks

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

# Approval state file (persistent between runs)
APPROVAL_STATE_FILE = os.path.join(WORKSPACE, ".telegram_approvals.json")


def _load_approval_state():
    """Load pending approval states from disk."""
    if os.path.exists(APPROVAL_STATE_FILE):
        with open(APPROVAL_STATE_FILE) as f:
            return json.load(f)
    return {}


def _save_approval_state(state):
    """Save approval states to disk."""
    os.makedirs(os.path.dirname(APPROVAL_STATE_FILE), exist_ok=True)
    with open(APPROVAL_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


async def send_carousel_preview(username, chat_id=None):
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

    post_path = post_files[0]
    with open(post_path) as f:
        post = json.load(f)

    print("[Telegram] Sending carousel preview for @%s to chat %s..." % (username, target_chat))

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

    # Save approval state
    state = _load_approval_state()
    state[username] = {
        "type": "carousel",
        "post_path": str(post_path),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    _save_approval_state(state)

    print("[Telegram] Carousel preview sent successfully!")


async def send_reel_approval(username, frame_path, ref_video_path,
                              prompt="", concept="", chat_id=None):
    """
    Send reel approval request to Telegram.

    CRITICAL WORKFLOW:
    1. Send Z-Image generated frame (starting frame for Kling)
    2. Send reference video (motion source)
    3. Send prompt details
    4. Wait for Approve/Reject BEFORE triggering expensive Kling generation

    This saves money by only running Kling when the user approves the frame + video combo.
    """
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
        print("[ERROR] TELEGRAM_CHAT_ID not set")
        sys.exit(1)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    print("[Telegram] Sending reel approval request for @%s..." % username)

    # 1. Send header explaining the approval
    header = (
        "<b>Reel Approval Request</b>\n\n"
        "Before generating the video in Kling ($$), please review:\n"
        "1. <b>Z-Image Frame</b> - the starting frame for the reel\n"
        "2. <b>Reference Video</b> - the motion source\n\n"
        "Concept: <b>%s</b>\n"
        "Source: @%s\n\n"
        "Approve ONLY if frame quality and video motion match your vision."
    ) % (concept or "N/A", username)

    await bot.send_message(chat_id=target_chat, text=header, parse_mode=ParseMode.HTML)

    # 2. Send Z-Image frame (starting frame)
    if os.path.exists(frame_path):
        frame_caption = (
            "Z-Image Starting Frame\n"
            "This will be the first frame of the reel.\n"
            "Check: face quality, pose, outfit, background"
        )
        with open(frame_path, "rb") as photo:
            await bot.send_photo(chat_id=target_chat, photo=photo, caption=frame_caption)
        print("[Telegram] Sent Z-Image frame: %s" % frame_path)
    else:
        await bot.send_message(chat_id=target_chat, text="[Frame not found: %s]" % frame_path)
        print("[Telegram] WARNING: Frame not found: %s" % frame_path)

    # 3. Send reference video (motion source)
    if os.path.exists(ref_video_path):
        video_caption = (
            "Reference Video (Motion Source)\n"
            "Kling will transfer this movement to the Z-Image frame above."
        )
        with open(ref_video_path, "rb") as video:
            await bot.send_video(
                chat_id=target_chat, video=video,
                caption=video_caption,
                supports_streaming=True,
            )
        print("[Telegram] Sent reference video: %s" % ref_video_path)
    else:
        await bot.send_message(
            chat_id=target_chat, text="[Reference video not found: %s]" % ref_video_path)
        print("[Telegram] WARNING: Reference video not found: %s" % ref_video_path)

    # 4. Send prompt details
    if prompt:
        prompt_msg = "<b>Generation Prompt:</b>\n<code>%s</code>" % prompt[:1000]
        await bot.send_message(chat_id=target_chat, text=prompt_msg, parse_mode=ParseMode.HTML)

    # 5. Send approve/reject/retry buttons
    approval_id = "reel_%s_%s" % (
        username,
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Approve (Generate in Kling)",
                callback_data="reel_approve_%s" % approval_id,
            ),
        ],
        [
            InlineKeyboardButton(
                "Reject",
                callback_data="reel_reject_%s" % approval_id,
            ),
            InlineKeyboardButton(
                "Retry Z-Image",
                callback_data="reel_retry_%s" % approval_id,
            ),
        ],
    ])

    await bot.send_message(
        chat_id=target_chat,
        text=(
            "<b>Action required:</b>\n"
            "- <b>Approve</b> = Start Kling generation (costs credits)\n"
            "- <b>Reject</b> = Discard this frame\n"
            "- <b>Retry Z-Image</b> = Regenerate the starting frame"
        ),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    print("[Telegram] Sent reel approval buttons (id: %s)" % approval_id)

    # Save approval state for later processing
    state = _load_approval_state()
    state[approval_id] = {
        "type": "reel",
        "username": username,
        "frame_path": frame_path,
        "ref_video_path": ref_video_path,
        "prompt": prompt,
        "concept": concept,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    _save_approval_state(state)

    print("[Telegram] Reel approval request sent! Waiting for user decision...")
    return approval_id


async def poll_approval(approval_id, timeout=3600):
    """
    Poll for approval status by checking the approval state file.
    Returns: "approved", "rejected", "retry", or "timeout"
    """
    import time
    start = time.time()
    check_interval = 10

    while time.time() - start < timeout:
        state = _load_approval_state()
        entry = state.get(approval_id, {})
        status = entry.get("status", "pending")

        if status in ("approved", "rejected", "retry"):
            print("[Telegram] Approval result: %s (reason: %s)" % (
                status, entry.get("reason", "N/A")))

            # Save to memory
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from agent_memory import AgentMemory
                mem = AgentMemory()
                if entry.get("type") == "reel":
                    mem.log_event("telegram", "reel_approval", {
                        "approval_id": approval_id,
                        "status": status,
                        "username": entry.get("username", ""),
                        "concept": entry.get("concept", ""),
                    }, lesson="Reel for @%s: %s - %s" % (
                        entry.get("username", "?"), status,
                        entry.get("reason", "no reason")))
                mem.close()
            except Exception as e:
                print("[Telegram] Warning: Could not save to memory: %s" % e)

            return status

        time.sleep(check_interval)

    print("[Telegram] Approval timed out after %ds" % timeout)
    return "timeout"


async def start_listener(chat_id=None):
    """
    Start Telegram bot listener for approval callbacks.

    Handles button presses:
    - approve_<username> / reject_<username> -- carousel approvals
    - reel_approve_<id> / reel_reject_<id> / reel_retry_<id> -- reel approvals
    """
    try:
        from telegram import Update
        from telegram.ext import Application, CallbackQueryHandler, ContextTypes
    except ImportError:
        print("[ERROR] python-telegram-bot not installed")
        sys.exit(1)

    if not TELEGRAM_BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    target_chat = chat_id or TELEGRAM_CHAT_ID

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        data = query.data
        user_id = str(query.from_user.id)

        if target_chat and user_id != target_chat:
            await query.edit_message_text("Unauthorized user.")
            return

        state = _load_approval_state()
        now = datetime.now(timezone.utc).isoformat()

        # Carousel approvals
        if data.startswith("approve_"):
            username = data.replace("approve_", "")
            if username in state:
                state[username]["status"] = "approved"
                state[username]["decided_at"] = now
            await query.edit_message_text("Carousel for @%s APPROVED! Publishing..." % username)
            print("[Listener] Carousel @%s approved" % username)

        elif data.startswith("reject_"):
            username = data.replace("reject_", "")
            if username in state:
                state[username]["status"] = "rejected"
                state[username]["decided_at"] = now
            await query.edit_message_text("Carousel for @%s REJECTED." % username)
            print("[Listener] Carousel @%s rejected" % username)

        elif data.startswith("regen_"):
            username = data.replace("regen_", "")
            if username in state:
                state[username]["status"] = "retry"
                state[username]["decided_at"] = now
            await query.edit_message_text("Carousel for @%s -- regenerating..." % username)
            print("[Listener] Carousel @%s regenerate requested" % username)

        # Reel approvals
        elif data.startswith("reel_approve_"):
            approval_id = data.replace("reel_approve_", "")
            if approval_id in state:
                state[approval_id]["status"] = "approved"
                state[approval_id]["decided_at"] = now
            await query.edit_message_text(
                "Reel APPROVED! Starting Kling generation (this may take 3-5 minutes)..."
            )
            print("[Listener] Reel %s approved -- triggering Kling" % approval_id)

        elif data.startswith("reel_reject_"):
            approval_id = data.replace("reel_reject_", "")
            if approval_id in state:
                state[approval_id]["status"] = "rejected"
                state[approval_id]["decided_at"] = now
                # Save rejected pattern to memory
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    from agent_memory import AgentMemory
                    mem = AgentMemory()
                    entry = state[approval_id]
                    mem.save_pattern(
                        pattern_type="rejected_reel_frame",
                        pattern_data={
                            "frame_path": entry.get("frame_path", ""),
                            "concept": entry.get("concept", ""),
                            "prompt": entry.get("prompt", "")[:500],
                        },
                        source_username=entry.get("username", ""),
                        score=0.0,
                    )
                    mem.close()
                except Exception as e:
                    print("[Listener] Warning: Could not save rejection to memory: %s" % e)

            await query.edit_message_text("Reel REJECTED. Frame discarded, pattern saved for learning.")
            print("[Listener] Reel %s rejected" % approval_id)

        elif data.startswith("reel_retry_"):
            approval_id = data.replace("reel_retry_", "")
            if approval_id in state:
                state[approval_id]["status"] = "retry"
                state[approval_id]["decided_at"] = now
            await query.edit_message_text("Reel -- regenerating Z-Image frame with new seed...")
            print("[Listener] Reel %s retry requested" % approval_id)

        _save_approval_state(state)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("[Listener] Starting Telegram approval listener...")
    print("[Listener] Waiting for button presses (Ctrl+C to stop)...")
    await app.run_polling(drop_pending_updates=True)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 telegram_preview.py <username>                    # Carousel preview")
        print("  python3 telegram_preview.py <username> --reel --frame F --ref-video V  # Reel approval")
        print("  python3 telegram_preview.py --listen                      # Start approval listener")
        sys.exit(1)

    # Listener mode
    if sys.argv[1] == "--listen":
        chat_id = None
        if "--chat-id" in sys.argv:
            idx = sys.argv.index("--chat-id")
            chat_id = sys.argv[idx + 1]
        asyncio.run(start_listener(chat_id))
        return

    username = sys.argv[1]
    chat_id = None

    if "--chat-id" in sys.argv:
        idx = sys.argv.index("--chat-id")
        chat_id = sys.argv[idx + 1]

    # Reel approval mode
    if "--reel" in sys.argv:
        frame_path = ""
        ref_video_path = ""
        prompt = ""
        concept = ""

        if "--frame" in sys.argv:
            idx = sys.argv.index("--frame")
            frame_path = sys.argv[idx + 1]
        if "--ref-video" in sys.argv:
            idx = sys.argv.index("--ref-video")
            ref_video_path = sys.argv[idx + 1]
        if "--prompt" in sys.argv:
            idx = sys.argv.index("--prompt")
            prompt = sys.argv[idx + 1]
        if "--concept" in sys.argv:
            idx = sys.argv.index("--concept")
            concept = sys.argv[idx + 1]

        if not frame_path or not ref_video_path:
            print("[ERROR] --reel requires --frame and --ref-video")
            sys.exit(1)

        asyncio.run(send_reel_approval(
            username, frame_path, ref_video_path,
            prompt=prompt, concept=concept, chat_id=chat_id,
        ))
        return

    # Default: carousel preview
    asyncio.run(send_carousel_preview(username, chat_id))


if __name__ == "__main__":
    main()
