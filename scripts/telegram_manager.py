#!/usr/bin/env python3
"""
Telegram Manager — package-level preview, approval buttons, text review.

Handles all Telegram interactions for the package pipeline:
- Send package preview (carousel of generated images)
- Approval/reject/regenerate buttons
- Text preview (caption + hashtags)
- Reel start frame approval (before Kling render)
- Status notifications

Usage:
    from telegram_manager import TelegramManager
    tm = TelegramManager()
    await tm.send_package_preview(package_id, state, pkg_dir)
    await tm.send_text_preview(package_id, publish_data)
    await tm.send_reel_frame_preview(package_id, asset, frame_path)
    await tm.notify(text)

Requires:
    pip3 install python-telegram-bot==20.7
    Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import sys
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


class TelegramManager:
    """Manages all Telegram interactions for package pipeline."""

    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.bot = None

    async def _init_bot(self):
        """Lazy-init Telegram bot."""
        if self.bot is None:
            from telegram import Bot
            from telegram.request import HTTPXRequest
            request = HTTPXRequest(
                read_timeout=60, write_timeout=60, connect_timeout=30)
            self.bot = Bot(token=self.bot_token, request=request)

    async def notify(self, text):
        """Send a plain text notification."""
        await self._init_bot()
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            print("[TelegramManager] Send error: %s" % str(e))

    async def send_package_preview(self, package_id, state, pkg_dir):
        """
        Send all generated asset images as a preview carousel in Telegram.
        Includes Approve All / Reject All / Regenerate buttons.

        Returns: list of sent message_ids
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        await self._init_bot()
        message_ids = []

        # Count stats
        assets = state.get("assets", [])
        total = len(assets)
        char_count = sum(1 for a in assets if a.get("has_character"))
        world_count = total - char_count
        generated = sum(1 for a in assets
                        if a.get("versions") and len(a["versions"]) > 0)

        # Header message
        header = (
            "Package Preview: %s\n"
            "Theme: %s\n"
            "Assets: %d total (%d character, %d world)\n"
            "Generated: %d/%d\n"
            "─────────────────" % (
                package_id[:35], state.get("theme", "?"),
                total, char_count, world_count,
                generated, total))

        msg = await self.bot.send_message(chat_id=self.chat_id, text=header)
        message_ids.append(msg.message_id)

        # Send each asset image
        sorted_assets = sorted(assets, key=lambda a: (
            {"post_slide": 0, "story_frame": 1, "reel_start_frame": 2}.get(a["type"], 3),
            a.get("order", 0)))

        for asset in sorted_assets:
            active_ver = asset.get("active_version", 0)
            versions = asset.get("versions", [])
            if active_ver <= 0 or not versions:
                continue

            file_rel = versions[active_ver - 1].get("file", "")
            file_path = os.path.join(pkg_dir, file_rel)

            if not os.path.exists(file_path):
                print("[TelegramManager] File not found: %s" % file_path)
                continue

            # Build caption
            role_icon = "👤" if asset.get("has_character") else "🌍"
            type_label = asset["type"].replace("_", " ").title()
            caption = "%s %s | %s | v%d | %s" % (
                role_icon, asset["asset_id"], type_label,
                active_ver, asset.get("generator", "?"))

            try:
                with open(file_path, "rb") as photo:
                    msg = await self.bot.send_photo(
                        chat_id=self.chat_id, photo=photo, caption=caption)
                    message_ids.append(msg.message_id)
            except Exception as e:
                print("[TelegramManager] Error sending %s: %s" % (
                    asset["asset_id"], e))

        # Action buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Approve All", callback_data="pkg_approve_%s" % package_id),
                InlineKeyboardButton(
                    "Reject All", callback_data="pkg_reject_%s" % package_id),
            ],
            [
                InlineKeyboardButton(
                    "Regenerate...", callback_data="pkg_regen_%s" % package_id),
            ],
        ])

        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text="Choose action for this package:",
            reply_markup=keyboard)
        message_ids.append(msg.message_id)

        return message_ids

    async def send_text_preview(self, package_id, publish_data):
        """
        Send text preview (caption + hashtags + story overlays) for approval.

        Returns: message_id
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        await self._init_bot()

        post = publish_data.get("post", {})
        caption = post.get("caption", "N/A")
        hashtags = post.get("hashtags", [])
        stories = publish_data.get("stories", [])

        text = (
            "Text Preview:\n\n"
            "Caption:\n%s\n\n"
            "Hashtags: %s\n"
            "Caption length: %d chars" % (
                caption, " ".join(hashtags), len(caption)))

        if stories:
            text += "\n\nStory overlays:"
            for s in stories:
                overlay = s.get("text_overlay", "none")
                text += "\n• %s: %s" % (s.get("asset_id", "?"), overlay)

        reel = publish_data.get("reel")
        if reel:
            text += "\n\nReel caption: %s" % reel.get("caption", "N/A")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Approve Text", callback_data="txt_approve_%s" % package_id),
                InlineKeyboardButton(
                    "Reject Text", callback_data="txt_reject_%s" % package_id),
            ],
        ])

        msg = await self.bot.send_message(
            chat_id=self.chat_id, text=text, reply_markup=keyboard)
        return msg.message_id

    async def send_reel_frame_preview(self, package_id, asset_id, frame_path):
        """
        Send reel start frame for approval before launching Kling render.

        Returns: message_id
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        await self._init_bot()

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Approve Frame → Start Kling",
                    callback_data="reel_frame_approve_%s" % package_id),
                InlineKeyboardButton(
                    "Reject Frame",
                    callback_data="reel_frame_reject_%s" % package_id),
            ],
        ])

        caption = (
            "Reel Start Frame Preview\n"
            "Package: %s\n"
            "Asset: %s\n\n"
            "Approve to start Kling motion render (3-5 min, costs tokens).\n"
            "Reject to regenerate the frame." % (package_id[:30], asset_id))

        if os.path.exists(frame_path):
            with open(frame_path, "rb") as photo:
                msg = await self.bot.send_photo(
                    chat_id=self.chat_id, photo=photo,
                    caption=caption, reply_markup=keyboard)
        else:
            msg = await self.bot.send_message(
                chat_id=self.chat_id, text=caption + "\n\n(Frame file not found)",
                reply_markup=keyboard)

        return msg.message_id

    async def send_regen_options(self, package_id, assets):
        """
        Show per-asset regeneration buttons.

        Returns: message_id
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        await self._init_bot()

        buttons = []
        for asset in assets:
            role_icon = "👤" if asset.get("has_character") else "🌍"
            label = "%s %s (%s)" % (
                role_icon, asset["asset_id"],
                asset["type"].replace("_", " "))
            callback = "regen_asset_%s___%s" % (package_id, asset["asset_id"])
            buttons.append([InlineKeyboardButton(label, callback_data=callback)])

        # Add cancel button
        buttons.append([InlineKeyboardButton(
            "Cancel", callback_data="pkg_approve_%s" % package_id)])

        keyboard = InlineKeyboardMarkup(buttons)
        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text="Select asset to regenerate:",
            reply_markup=keyboard)
        return msg.message_id

    async def send_kling_result(self, package_id, video_path):
        """Send completed Kling video for review."""
        await self._init_bot()

        if os.path.exists(video_path):
            with open(video_path, "rb") as video:
                msg = await self.bot.send_video(
                    chat_id=self.chat_id, video=video,
                    caption="Kling motion video for %s" % package_id[:30])
            return msg.message_id
        else:
            await self.notify("Kling video file not found: %s" % video_path)
            return None

    async def send_final_summary(self, package_id, state):
        """Send final approval summary."""
        await self._init_bot()

        assets = state.get("assets", [])
        text_data = state.get("text", {})

        text = (
            "PACKAGE APPROVED\n"
            "─────────────────\n"
            "ID: %s\n"
            "Theme: %s\n"
            "Assets: %d\n"
            "Caption: %s\n"
            "Hashtags: %s\n"
            "Status: %s" % (
                package_id,
                state.get("theme", "?"),
                len(assets),
                text_data.get("post_caption", "N/A")[:100],
                " ".join(text_data.get("post_hashtags", [])),
                state.get("status", "?")))

        await self.bot.send_message(chat_id=self.chat_id, text=text)


# ── CLI ──────────────────────────────────────────────────────────

def main():
    import asyncio

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 telegram_manager.py notify <text>")
        print("  python3 telegram_manager.py test")
        return

    cmd = sys.argv[1]
    tm = TelegramManager()

    if cmd == "notify":
        text = " ".join(sys.argv[2:]) or "Test notification from TelegramManager"
        asyncio.run(tm.notify(text))
        print("Sent.")

    elif cmd == "test":
        asyncio.run(tm.notify(
            "TelegramManager test\n"
            "Bot connected. Package preview system ready."))
        print("Test message sent.")

    else:
        print("Unknown command: %s" % cmd)


if __name__ == "__main__":
    main()
