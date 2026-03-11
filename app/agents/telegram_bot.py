"""Telegram Bot — human-in-the-loop approval for Instagram posts.

Sends post drafts to a Telegram chat for review.
User can approve, reject, or request edits before posting.
"""

import asyncio
import base64
import json
import logging
import os
from typing import Optional, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")
    return token


def _get_chat_id() -> str:
    """Get the chat ID to send messages to.

    Can be set via TELEGRAM_CHAT_ID env var, or discovered
    from the first /start message.
    """
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        raise ValueError(
            "TELEGRAM_CHAT_ID not set. Send /start to the bot first, "
            "then set the TELEGRAM_CHAT_ID env variable."
        )
    return chat_id


async def send_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    reply_markup: Optional[dict] = None,
) -> dict:
    """Send a text message via Telegram bot.

    Args:
        text: Message text (supports HTML formatting).
        chat_id: Override chat ID (uses env default if None).
        parse_mode: 'HTML' or 'Markdown'.
        reply_markup: Optional inline keyboard markup.

    Returns:
        Telegram API response dict.
    """
    token = _get_bot_token()
    target_chat = chat_id or _get_chat_id()

    payload: dict = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def send_photo(
    photo_url: Optional[str] = None,
    photo_bytes: Optional[bytes] = None,
    caption: str = "",
    chat_id: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> dict:
    """Send a photo via Telegram bot.

    Args:
        photo_url: URL of the photo to send.
        photo_bytes: Raw photo bytes (alternative to URL).
        caption: Photo caption.
        chat_id: Override chat ID.
        reply_markup: Optional inline keyboard markup.

    Returns:
        Telegram API response dict.
    """
    token = _get_bot_token()
    target_chat = chat_id or _get_chat_id()

    if photo_url:
        payload: dict = {
            "chat_id": target_chat,
            "photo": photo_url,
            "caption": caption[:1024],  # Telegram caption limit
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{token}/sendPhoto",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    elif photo_bytes:
        data = {
            "chat_id": target_chat,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{token}/sendPhoto",
                data=data,
                files=files,
            )
            resp.raise_for_status()
            return resp.json()

    else:
        raise ValueError("Either photo_url or photo_bytes must be provided")


async def send_post_for_approval(
    post_package: dict,
    chat_id: Optional[str] = None,
) -> str:
    """Send a complete post package to Telegram for human approval.

    Sends each image as a photo, then the caption with approve/reject buttons.

    Args:
        post_package: Output from publish_agent.assemble_post().
        chat_id: Override chat ID.

    Returns:
        Message ID of the approval message (for tracking).
    """
    images = post_package.get("images", [])
    caption = post_package.get("full_caption", post_package.get("caption", ""))
    avg_qc = post_package.get("avg_qc_score", 0)
    qc_passed = post_package.get("qc_passed", False)
    qc_score = post_package.get("qc_score", avg_qc)
    qc_issues = post_package.get("qc_issues", [])
    qc_feedback = post_package.get("qc_feedback", "")

    # QC status indicator
    qc_status = "PASSED" if qc_passed else "REJECTED (best attempt)"
    issues_text = ""
    if qc_issues:
        issues_text = "\n".join(f"  - {issue}" for issue in qc_issues[:5])
        issues_text = f"\n<b>Issues:</b>\n{issues_text}"

    # Header message
    header = (
        f"<b>New Post Draft</b>\n"
        f"Type: {post_package.get('type', 'single')}\n"
        f"Images: {post_package.get('image_count', len(images))}\n"
        f"QC: {qc_score}/10 — <b>{qc_status}</b>"
        f"{issues_text}\n"
        f"---"
    )
    if qc_feedback:
        header += f"\n<i>{qc_feedback[:200]}</i>"
    await send_message(header, chat_id=chat_id)

    # Send each image
    for i, img in enumerate(images):
        img_caption = f"Image {i + 1}/{len(images)}: {img.get('theme', '')}\nQC: {img.get('qc_score', '?')}/10"

        if img.get("image_url"):
            try:
                await send_photo(
                    photo_url=img["image_url"],
                    caption=img_caption,
                    chat_id=chat_id,
                )
            except Exception as e:
                logger.warning(f"Failed to send photo URL: {e}")
                await send_message(f"[Image {i + 1} - URL: {img['image_url']}]", chat_id=chat_id)

        elif img.get("image_base64"):
            try:
                photo_bytes = base64.b64decode(img["image_base64"])
                await send_photo(
                    photo_bytes=photo_bytes,
                    caption=img_caption,
                    chat_id=chat_id,
                )
            except Exception as e:
                logger.warning(f"Failed to send base64 photo: {e}")
                await send_message(f"[Image {i + 1} - could not send]", chat_id=chat_id)
        else:
            await send_message(f"[Image {i + 1} - no image data]", chat_id=chat_id)

        # Small delay between messages to avoid rate limiting
        await asyncio.sleep(0.5)

    # Send caption with approval buttons
    approval_text = (
        f"<b>Caption:</b>\n{caption}\n\n"
        f"---\n"
        f"Approve this post?"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": "approve"},
                {"text": "Reject", "callback_data": "reject"},
            ],
            [
                {"text": "Edit Caption", "callback_data": "edit_caption"},
                {"text": "Regenerate", "callback_data": "regenerate"},
            ],
        ],
    }

    result = await send_message(
        approval_text,
        chat_id=chat_id,
        reply_markup=keyboard,
    )

    msg_id = str(result.get("result", {}).get("message_id", ""))
    return msg_id


async def get_updates(
    offset: Optional[int] = None,
    timeout: int = 30,
) -> list[dict]:
    """Get new updates (messages/callbacks) from Telegram.

    Args:
        offset: Update offset for long polling.
        timeout: Long polling timeout in seconds.

    Returns:
        List of update dicts.
    """
    token = _get_bot_token()
    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        resp = await client.get(
            f"{TELEGRAM_API}/bot{token}/getUpdates",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    return data.get("result", [])


async def wait_for_approval(
    timeout_minutes: int = 60,
) -> dict:
    """Wait for user to approve or reject the post via Telegram callback.

    Args:
        timeout_minutes: Max time to wait for response.

    Returns:
        dict with 'decision' ('approve'|'reject'|'edit_caption'|'regenerate'|'timeout'),
        and optionally 'new_caption' if edit was requested.
    """
    offset: Optional[int] = None
    deadline = asyncio.get_event_loop().time() + (timeout_minutes * 60)

    while asyncio.get_event_loop().time() < deadline:
        try:
            updates = await get_updates(offset=offset, timeout=10)
        except Exception as e:
            logger.warning(f"Error polling Telegram: {e}")
            await asyncio.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1

            # Check for callback query (button press)
            callback = update.get("callback_query")
            if callback:
                decision = callback.get("data", "")
                if decision in ("approve", "reject", "edit_caption", "regenerate"):
                    # Acknowledge the callback
                    await _answer_callback(callback["id"], f"Got it: {decision}")
                    return {"decision": decision}

            # Check for text message (could be new caption)
            message = update.get("message", {})
            text = message.get("text", "")
            if text and not text.startswith("/"):
                return {"decision": "edit_caption", "new_caption": text}

    return {"decision": "timeout"}


async def _answer_callback(callback_id: str, text: str) -> None:
    """Answer a Telegram callback query."""
    token = _get_bot_token()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{TELEGRAM_API}/bot{token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
            )
    except Exception as e:
        logger.warning(f"Failed to answer callback: {e}")


async def discover_chat_id() -> Optional[str]:
    """Discover chat ID by checking recent messages to the bot.

    User should send /start to the bot first.

    Returns:
        Chat ID string, or None if not found.
    """
    try:
        updates = await get_updates(timeout=1)
        for update in updates:
            message = update.get("message", {})
            chat = message.get("chat", {})
            if chat.get("id"):
                return str(chat["id"])
    except Exception:
        pass
    return None
