"""
Telegram Manager — single entry point for all Telegram communication.

ONLY mc_conductor.py should use this module.
No other module should talk to Telegram directly.

Handles:
  - Sending text messages
  - Sending images (single + album)
  - Sending videos
  - Inline keyboard buttons (approve/reject/regenerate)
  - Receiving callback queries
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from content_module.core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramManager:
    """Manages all Telegram communication for the content module."""

    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.app: Optional[Application] = None
        self._command_handlers: dict = {}
        self._callback_handler = None
        self._message_handler = None

    async def init_bot(self) -> None:
        """Initialize the Telegram bot."""
        if not TELEGRAM_BOT_TOKEN:
            raise Exception("TELEGRAM_BOT_TOKEN not set")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        me = await self.bot.get_me()
        print(f"[Telegram] Bot initialized: @{me.username}")

    async def send_text(self, text: str, parse_mode: str = "HTML") -> None:
        """Send a text message to the configured chat."""
        if not self.bot:
            await self.init_bot()
        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=parse_mode,
        )

    async def send_image(
        self,
        image_path: str,
        caption: str = "",
    ) -> None:
        """Send a single image to the chat."""
        if not self.bot:
            await self.init_bot()
        with open(image_path, "rb") as f:
            await self.bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=f,
                caption=caption[:1024] if caption else None,
                parse_mode="HTML",
            )

    async def send_album(
        self,
        image_paths: list[str],
        caption: str = "",
    ) -> None:
        """Send multiple images as an album (media group)."""
        if not self.bot:
            await self.init_bot()

        if not image_paths:
            return

        if len(image_paths) == 1:
            await self.send_image(image_paths[0], caption)
            return

        media = []
        files_to_close = []
        for i, path in enumerate(image_paths[:10]):
            f = open(path, "rb")
            files_to_close.append(f)
            media.append(InputMediaPhoto(
                media=f,
                caption=caption[:1024] if i == 0 and caption else None,
                parse_mode="HTML" if i == 0 and caption else None,
            ))

        try:
            await self.bot.send_media_group(
                chat_id=TELEGRAM_CHAT_ID,
                media=media,
            )
        finally:
            for f in files_to_close:
                f.close()

    async def send_video(
        self,
        video_path: str,
        caption: str = "",
    ) -> None:
        """Send a video to the chat."""
        if not self.bot:
            await self.init_bot()
        with open(video_path, "rb") as f:
            await self.bot.send_video(
                chat_id=TELEGRAM_CHAT_ID,
                video=f,
                caption=caption[:1024] if caption else None,
                parse_mode="HTML",
                supports_streaming=True,
            )

    async def send_job_preview(
        self,
        job_id: str,
        content_type: str,
        assets: list[dict],
        plan_summary: str = "",
    ) -> None:
        """
        Send a preview of generated content with approve/reject buttons.

        Args:
            job_id: Job ID
            content_type: 'post', 'story', or 'reel'
            assets: List of asset dicts with 'file_path' and 'role'
            plan_summary: Brief text about the plan
        """
        header = f"<b>{content_type.upper()} Preview</b>\n"
        header += f"<code>{job_id}</code>\n\n"
        if plan_summary:
            header += f"{plan_summary}\n\n"

        # List assets
        for i, asset in enumerate(assets):
            role = asset.get("role", f"asset_{i}")
            status_icon = "✅" if asset.get("status") == "approved" else "🖼"
            gen = asset.get("generator", "?")
            header += f"{status_icon} {i+1}. {role} ({gen})\n"

        await self.send_text(header)

        # Send images/videos
        image_paths = []
        for asset in assets:
            fp = asset.get("file_path", "")
            if fp and Path(fp).exists():
                if fp.endswith(".mp4"):
                    await self.send_video(fp, caption=f"Reel: {asset.get('role', '')}")
                else:
                    image_paths.append(fp)

        if image_paths:
            await self.send_album(image_paths)

        # Approval buttons
        buttons = [
            [
                InlineKeyboardButton("✅ Approve All", callback_data=f"approve_all:{job_id}"),
                InlineKeyboardButton("❌ Reject All", callback_data=f"reject_all:{job_id}"),
            ],
        ]

        # Per-asset regenerate buttons
        for i, asset in enumerate(assets):
            if asset.get("file_path") and asset.get("status") != "approved":
                buttons.append([
                    InlineKeyboardButton(
                        f"🔄 Regen #{i+1} ({asset.get('role', '')})",
                        callback_data=f"regen:{job_id}:{i}",
                    )
                ])

        markup = InlineKeyboardMarkup(buttons)
        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="Choose an action:",
            reply_markup=markup,
        )

    async def send_text_review(
        self,
        job_id: str,
        text_data: dict,
    ) -> None:
        """Send generated text for review."""
        caption = text_data.get("caption", "")
        hashtags = text_data.get("hashtags", [])
        alt_caption = text_data.get("alt_caption", "")

        msg = f"<b>Text Review</b>\n<code>{job_id}</code>\n\n"
        msg += f"<b>Caption:</b>\n{caption}\n\n"
        if hashtags:
            msg += f"<b>Hashtags:</b> {' '.join(hashtags)}\n\n"
        if alt_caption:
            msg += f"<b>Alternative:</b>\n{alt_caption}\n"

        buttons = [
            [
                InlineKeyboardButton("✅ Approve Text", callback_data=f"approve_text:{job_id}"),
                InlineKeyboardButton("🔄 Regenerate Text", callback_data=f"regen_text:{job_id}"),
            ],
        ]
        markup = InlineKeyboardMarkup(buttons)

        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode="HTML",
            reply_markup=markup,
        )

    async def send_reel_start_frame(
        self,
        job_id: str,
        frame_path: str,
    ) -> None:
        """Send reel start frame for approval before Kling render."""
        await self.send_image(
            frame_path,
            caption=f"<b>Reel Start Frame</b>\n<code>{job_id}</code>\n\n"
                    "Approve to start Kling video generation.",
        )

        buttons = [
            [
                InlineKeyboardButton(
                    "✅ Approve → Start Kling",
                    callback_data=f"approve_frame:{job_id}",
                ),
                InlineKeyboardButton(
                    "🔄 Regenerate Frame",
                    callback_data=f"regen_frame:{job_id}",
                ),
            ],
        ]
        markup = InlineKeyboardMarkup(buttons)
        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="Approve start frame?",
            reply_markup=markup,
        )

    def build_application(
        self,
        command_handlers: dict,
        callback_handler,
        message_handler=None,
    ) -> Application:
        """
        Build the Telegram Application with handlers.

        Args:
            command_handlers: Dict of command_name -> async handler function
            callback_handler: Async function to handle callback queries
            message_handler: Async function to handle free text messages

        Returns:
            Configured Application
        """
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        for cmd, handler_fn in command_handlers.items():
            self.app.add_handler(CommandHandler(cmd, handler_fn))

        self.app.add_handler(CallbackQueryHandler(callback_handler))

        if message_handler:
            self.app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
            )

        return self.app
