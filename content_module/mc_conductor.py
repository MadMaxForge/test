"""
MC Conductor — Main orchestrator for the Content Creation Module.

The MC is the ONLY entry point. It:
  - Manages the Telegram event loop (receives commands, sends previews)
  - Coordinates three separate flows: post / story / reel
  - Each flow: reference → analyst → planner → creative → generators → review → publish
  - Handles per-asset regeneration, approval, text review
  - Enforces: Kling only starts after start frame is approved

Usage:
    python3 -m content_module.mc_conductor              # Start event loop
    python3 -m content_module.mc_conductor --status      # Show all jobs

Requires:
    pip install python-telegram-bot==20.7 requests
    Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENROUTER_API_KEY,
                 EVOLINK_API_KEY, COMFYUI_URL (via SSH tunnel)
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from content_module.core.config import TELEGRAM_CHAT_ID
from content_module.core import state_manager, queue_manager, asset_router
from content_module.generators import z_image_generator, nano_banana_generator, kling_generator
from content_module.generators import instagram_scraper
from content_module.llm_agents import reference_analyst, planner, creative, publish
from content_module.telegram.telegram_manager import TelegramManager


class MCConductor:
    """Main orchestrator for the Instagram content creation pipeline."""

    def __init__(self) -> None:
        self.telegram = TelegramManager()
        self._processed_callbacks: OrderedDict = OrderedDict()

    # ── Main Event Loop ──────────────────────────────────────────

    async def start(self) -> None:
        """Start the MC event loop — listens for Telegram commands."""
        print("[MC] Starting MC Conductor event loop...")
        await self.telegram.init_bot()

        await self.telegram.send_text(
            "<b>MC Conductor started</b>\n\n"
            "Commands:\n"
            "/add &lt;url&gt; — add reference URL\n"
            "/next — process next reference\n"
            "/next_post — process next post reference\n"
            "/next_story &lt;theme&gt; — create story (optional theme)\n"
            "/next_reel — process next reel reference\n"
            "/status — show all jobs\n"
            "/queue — show reference queue\n"
            "/job &lt;id&gt; — show job details"
        )

        app = self.telegram.build_application(
            command_handlers={
                "add": self._cmd_add,
                "next": self._cmd_next,
                "next_post": self._cmd_next_post,
                "next_story": self._cmd_next_story,
                "next_reel": self._cmd_next_reel,
                "status": self._cmd_status,
                "queue": self._cmd_queue,
                "job": self._cmd_job,
            },
            callback_handler=self._handle_callback,
            message_handler=self._handle_message,
        )

        print("[MC] Polling for Telegram updates...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            print("[MC] Shutting down...")
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    # ── Telegram Commands ────────────────────────────────────────

    async def _cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /add <url> — add a reference to the queue."""
        if not self._is_authorized(update):
            return

        args = context.args
        if not args:
            await update.message.reply_text("Usage: /add <instagram_url>")
            return

        url = args[0]
        ref_type = queue_manager.detect_reference_type(url)
        entry = queue_manager.add_reference(url, ref_type)

        await update.message.reply_text(
            f"✅ Added {ref_type} reference\n"
            f"ID: <code>{entry['ref_id']}</code>\n"
            f"URL: {url}",
            parse_mode="HTML",
        )

    async def _cmd_next(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next — process next reference from queue (auto-detect type)."""
        if not self._is_authorized(update):
            return

        ref = queue_manager.get_next()
        if not ref:
            await update.message.reply_text("Queue is empty. Use /add <url> first.")
            return

        ref_type = ref["type"]
        if ref_type == "reel":
            await self._process_reel(ref, update)
        else:
            await self._process_post(ref, update)

    async def _cmd_next_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next_post — process next post reference."""
        if not self._is_authorized(update):
            return

        ref = queue_manager.get_next(ref_type="post")
        if not ref:
            await update.message.reply_text("No post references in queue.")
            return

        await self._process_post(ref, update)

    async def _cmd_next_story(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next_story [theme] — create story content."""
        if not self._is_authorized(update):
            return

        theme = " ".join(context.args) if context.args else ""

        # Stories can be reference-based or theme-based
        ref = queue_manager.get_next(ref_type="post")
        if ref:
            await self._process_story(ref, update, theme)
        elif theme:
            await self._process_story(None, update, theme)
        else:
            await update.message.reply_text(
                "Provide a theme: /next_story <theme>\n"
                "Or add a post reference first with /add <url>"
            )

    async def _cmd_next_reel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next_reel — process next reel reference."""
        if not self._is_authorized(update):
            return

        ref = queue_manager.get_next(ref_type="reel")
        if not ref:
            await update.message.reply_text("No reel references in queue.")
            return

        await self._process_reel(ref, update)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status — show all jobs."""
        if not self._is_authorized(update):
            return

        jobs = state_manager.list_jobs()
        if not jobs:
            await update.message.reply_text("No jobs yet.")
            return

        msg = "<b>Jobs:</b>\n\n"
        for j in jobs[-10:]:
            status_icon = {
                "draft": "📝", "analyzing": "🔍", "planning": "📋",
                "prompting": "✍️", "generating": "⚙️", "review": "👁",
                "revising": "🔄", "text_review": "📝", "approved": "✅",
                "failed": "❌",
            }.get(j["status"], "❓")
            msg += (
                f"{status_icon} <code>{j['job_id']}</code>\n"
                f"   {j['content_type']} | {j['status']}\n"
            )

        await update.message.reply_text(msg, parse_mode="HTML")

    async def _cmd_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /queue — show reference queue."""
        if not self._is_authorized(update):
            return

        stats = queue_manager.get_queue_stats()
        refs = queue_manager.list_references(status_filter="new")

        msg = (
            f"<b>Reference Queue</b>\n\n"
            f"📨 New posts: {stats['new_posts']}\n"
            f"🎬 New reels: {stats['new_reels']}\n"
            f"✅ Used: {stats['used']}\n"
            f"❌ Rejected: {stats['rejected']}\n"
            f"📊 Total: {stats['total']}\n"
        )

        if refs:
            msg += "\n<b>Pending:</b>\n"
            for r in refs[:5]:
                msg += f"  • {r['type']}: {r['url'][:50]}...\n"

        await update.message.reply_text(msg, parse_mode="HTML")

    async def _cmd_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /job <id> — show job details."""
        if not self._is_authorized(update):
            return

        if not context.args:
            await update.message.reply_text("Usage: /job <job_id>")
            return

        job_id = context.args[0]
        try:
            job = state_manager.get_job(job_id)
        except FileNotFoundError:
            await update.message.reply_text(f"Job not found: {job_id}")
            return

        msg = (
            f"<b>Job: {job_id}</b>\n\n"
            f"Type: {job['content_type']}\n"
            f"Status: {job['status']}\n"
            f"Created: {job['created_at'][:19]}\n"
            f"Reference: {job['reference_url']}\n\n"
        )

        if job["assets"]:
            msg += "<b>Assets:</b>\n"
            for a in job["assets"]:
                icon = "✅" if a["status"] == "approved" else "⏳" if a["status"] == "pending" else "🔄"
                msg += f"  {icon} {a['index']+1}. {a['role']} ({a['generator']}) — {a['status']}\n"

        if job.get("error"):
            msg += f"\n<b>Error:</b> {job['error']}"

        await update.message.reply_text(msg, parse_mode="HTML")

    # ── Callback Handler ─────────────────────────────────────────

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        data = query.data
        callback_id = query.id

        # Idempotency check
        if callback_id in self._processed_callbacks:
            return
        self._processed_callbacks[callback_id] = True
        # Keep only last 500
        while len(self._processed_callbacks) > 500:
            self._processed_callbacks.popitem(last=False)

        parts = data.split(":")
        action = parts[0]
        job_id = parts[1] if len(parts) > 1 else ""

        try:
            if action == "approve_all":
                await self._handle_approve_all(job_id)
            elif action == "reject_all":
                await self._handle_reject_all(job_id)
            elif action == "regen":
                asset_index = int(parts[2]) if len(parts) > 2 else 0
                await self._handle_regenerate(job_id, asset_index)
            elif action == "approve_text":
                await self._handle_approve_text(job_id)
            elif action == "regen_text":
                await self._handle_regenerate_text(job_id)
            elif action == "approve_frame":
                await self._handle_approve_frame(job_id)
            elif action == "regen_frame":
                await self._handle_regenerate_frame(job_id)
        except Exception as e:
            await self.telegram.send_text(f"❌ Error: {e}")
            traceback.print_exc()

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle free text messages (URL detection, feedback)."""
        if not self._is_authorized(update):
            return

        text = update.message.text.strip()

        # Auto-detect Instagram URLs
        if "instagram.com/" in text:
            ref_type = queue_manager.detect_reference_type(text)
            entry = queue_manager.add_reference(text, ref_type)
            await update.message.reply_text(
                f"✅ Added {ref_type} reference: <code>{entry['ref_id']}</code>",
                parse_mode="HTML",
            )
            return

        # Otherwise treat as general feedback (future: interpret with LLM)
        await update.message.reply_text(
            "💡 Send an Instagram URL to add to queue, or use commands:\n"
            "/add, /next, /status, /queue"
        )

    # ── Content Flows ────────────────────────────────────────────

    async def _process_post(self, ref: dict, update: Update) -> None:
        """Execute the full post creation flow."""
        job_id = None
        try:
            # 1. Create job
            job_id = state_manager.create_job("post", ref["url"], ref["type"])
            queue_manager.mark_used(ref["ref_id"], job_id)
            await self.telegram.send_text(
                f"🚀 <b>Starting post job</b>\n<code>{job_id}</code>\n{ref['url']}",
            )

            # 2. Download reference
            state_manager.update_job_status(job_id, "analyzing")
            await self.telegram.send_text("🔍 Downloading reference...")
            session_id = os.environ.get("INSTA_SESSION_ID", "")
            manifest = instagram_scraper.download_reference(ref["url"], job_id, session_id)

            # 3. Analyze reference
            await self.telegram.send_text("🔍 Analyzing reference...")
            analysis = reference_analyst.analyze_post_reference(manifest)
            state_manager.set_analysis(job_id, analysis)

            # 4. Plan post
            state_manager.update_job_status(job_id, "planning")
            await self.telegram.send_text("📋 Planning post structure...")
            plan = planner.plan_post(analysis)
            state_manager.set_plan(job_id, plan)

            # 5. Generate prompts
            state_manager.update_job_status(job_id, "prompting")
            await self.telegram.send_text("✍️ Writing prompts...")
            prompts = creative.generate_prompts(plan, analysis)
            state_manager.set_prompts(job_id, prompts)

            # 6. Generate images
            state_manager.update_job_status(job_id, "generating")
            await self.telegram.send_text(
                f"⚙️ Generating {len(prompts)} image(s)... This may take a few minutes."
            )
            await self._generate_assets(job_id, prompts, "post")

            # 7. Send preview
            state_manager.update_job_status(job_id, "review")
            job = state_manager.get_job(job_id)
            await self.telegram.send_job_preview(
                job_id=job_id,
                content_type="post",
                assets=job["assets"],
                plan_summary=f"Theme: {plan.get('theme', '?')} | {len(job['assets'])} slides",
            )

        except Exception as e:
            if job_id:
                state_manager.update_job_status(job_id, "failed", error=str(e))
            await self.telegram.send_text(f"❌ Post job failed: {e}")
            traceback.print_exc()

    async def _process_story(
        self,
        ref: Optional[dict],
        update: Update,
        theme: str = "",
    ) -> None:
        """Execute the full story creation flow."""
        job_id = None
        try:
            ref_url = ref["url"] if ref else f"theme:{theme}"
            job_id = state_manager.create_job("story", ref_url, "post")
            if ref:
                queue_manager.mark_used(ref["ref_id"], job_id)

            await self.telegram.send_text(
                f"🚀 <b>Starting story job</b>\n<code>{job_id}</code>",
            )

            # Analyze if we have a reference
            analysis = {}
            if ref:
                state_manager.update_job_status(job_id, "analyzing")
                await self.telegram.send_text("🔍 Downloading & analyzing reference...")
                session_id = os.environ.get("INSTA_SESSION_ID", "")
                manifest = instagram_scraper.download_reference(ref["url"], job_id, session_id)
                analysis = reference_analyst.analyze_post_reference(manifest)
                state_manager.set_analysis(job_id, analysis)

            # Plan story
            state_manager.update_job_status(job_id, "planning")
            await self.telegram.send_text("📋 Planning story...")
            plan = planner.plan_story(analysis, theme)
            state_manager.set_plan(job_id, plan)

            # Generate prompts
            state_manager.update_job_status(job_id, "prompting")
            await self.telegram.send_text("✍️ Writing prompts...")
            prompts = creative.generate_prompts(plan, analysis)
            state_manager.set_prompts(job_id, prompts)

            # Generate images
            state_manager.update_job_status(job_id, "generating")
            await self.telegram.send_text(
                f"⚙️ Generating {len(prompts)} story frame(s)..."
            )
            await self._generate_assets(job_id, prompts, "story")

            # Send preview
            state_manager.update_job_status(job_id, "review")
            job = state_manager.get_job(job_id)
            await self.telegram.send_job_preview(
                job_id=job_id,
                content_type="story",
                assets=job["assets"],
                plan_summary=f"Theme: {plan.get('theme', '?')} | {plan.get('total_frames', 0)} frames",
            )

        except Exception as e:
            if job_id:
                state_manager.update_job_status(job_id, "failed", error=str(e))
            await self.telegram.send_text(f"❌ Story job failed: {e}")
            traceback.print_exc()

    async def _process_reel(self, ref: dict, update: Update) -> None:
        """Execute the reel creation flow (two-stage: start frame → approve → Kling)."""
        job_id = None
        try:
            job_id = state_manager.create_job("reel", ref["url"], "reel")
            queue_manager.mark_used(ref["ref_id"], job_id)

            await self.telegram.send_text(
                f"🚀 <b>Starting reel job</b>\n<code>{job_id}</code>\n{ref['url']}",
            )

            # 1. Download reel reference
            state_manager.update_job_status(job_id, "analyzing")
            await self.telegram.send_text("🔍 Downloading reel reference...")
            session_id = os.environ.get("INSTA_SESSION_ID", "")
            manifest = instagram_scraper.download_reference(ref["url"], job_id, session_id)

            # 2. Analyze reel
            await self.telegram.send_text("🔍 Analyzing reel...")
            analysis = reference_analyst.analyze_reel_reference(manifest)
            state_manager.set_analysis(job_id, analysis)

            # 3. Plan reel
            state_manager.update_job_status(job_id, "planning")
            plan = planner.plan_reel(analysis)
            state_manager.set_plan(job_id, plan)

            # 4. Generate start frame prompt
            state_manager.update_job_status(job_id, "prompting")
            prompts = creative.generate_prompts(plan, analysis)
            state_manager.set_prompts(job_id, prompts)

            # 5. Generate ONLY the start frame (not the full reel yet)
            state_manager.update_job_status(job_id, "generating")
            await self.telegram.send_text("⚙️ Generating start frame...")

            start_prompt = None
            for p in prompts:
                if p.get("role") == "start_frame":
                    start_prompt = p
                    break

            if start_prompt:
                prompt_text = start_prompt["prompt"]
                state_manager.update_asset(job_id, 0, status="generating")
                img_bytes = z_image_generator.generate_image(prompt_text, content_type="reel")
                file_path = z_image_generator.save_image(img_bytes, job_id, 0)
                state_manager.update_asset(job_id, 0, status="generated", file_path=file_path)

                # 6. Send start frame for approval (DO NOT start Kling yet)
                state_manager.update_job_status(job_id, "review")
                await self.telegram.send_reel_start_frame(job_id, file_path)
            else:
                raise Exception("No start_frame prompt generated")

        except Exception as e:
            if job_id:
                state_manager.update_job_status(job_id, "failed", error=str(e))
            await self.telegram.send_text(f"❌ Reel job failed: {e}")
            traceback.print_exc()

    # ── Asset Generation ─────────────────────────────────────────

    async def _generate_assets(
        self,
        job_id: str,
        prompts: list[dict],
        content_type: str,
    ) -> None:
        """Generate all assets for a job."""
        for prompt_data in prompts:
            idx = prompt_data.get("index", 0)
            generator = prompt_data.get("generator", "")
            prompt_text = prompt_data.get("prompt", "")
            role = prompt_data.get("role", "")

            # Skip non-generatable assets
            if role in ("motion_reference", "final_render") or not prompt_text:
                continue

            state_manager.update_asset(job_id, idx, status="generating")

            try:
                if generator == "z_image":
                    img_bytes = z_image_generator.generate_image(prompt_text, content_type)
                    file_path = z_image_generator.save_image(img_bytes, job_id, idx)
                elif generator == "nano_banana":
                    img_bytes = nano_banana_generator.generate_image(prompt_text, content_type)
                    file_path = nano_banana_generator.save_image(img_bytes, job_id, idx)
                else:
                    print(f"[MC] Unknown generator: {generator} for asset {idx}")
                    state_manager.update_asset(job_id, idx, status="failed")
                    continue

                state_manager.update_asset(job_id, idx, status="generated", file_path=file_path)
                print(f"[MC] Asset {idx} generated: {file_path}")

            except Exception as e:
                print(f"[MC] Asset {idx} generation failed: {e}")
                state_manager.update_asset(job_id, idx, status="failed")

    # ── Callback Handlers ────────────────────────────────────────

    async def _handle_approve_all(self, job_id: str) -> None:
        """Approve all assets and move to text generation."""
        job = state_manager.get_job(job_id)
        approved_count = 0
        for i, asset in enumerate(job["assets"]):
            if asset["status"] in ("generated", "pending"):
                state_manager.approve_asset(job_id, i)
                approved_count += 1

        if approved_count == 0:
            await self.telegram.send_text(f"No assets to approve in {job_id}")
            return

        await self.telegram.send_text(f"✅ Approved {approved_count} asset(s)")

        # Save version snapshot
        state_manager.save_version_snapshot(job_id)

        # Move to text generation
        state_manager.update_job_status(job_id, "text_review")
        await self.telegram.send_text("📝 Generating caption & hashtags...")

        job = state_manager.get_job(job_id)
        plan = job.get("plan", {})
        analysis = job.get("analysis", {})
        content_type = job["content_type"]

        try:
            if content_type == "post":
                text_data = publish.generate_post_text(plan, analysis)
            elif content_type == "story":
                text_data = publish.generate_story_text(plan)
            elif content_type == "reel":
                text_data = publish.generate_reel_text(plan)
            else:
                text_data = {"caption": "", "hashtags": []}

            state_manager.set_text(job_id, text_data)
            await self.telegram.send_text_review(job_id, text_data)

        except Exception as e:
            await self.telegram.send_text(f"❌ Text generation failed: {e}")

    async def _handle_reject_all(self, job_id: str) -> None:
        """Reject all assets."""
        job = state_manager.get_job(job_id)
        for i, asset in enumerate(job["assets"]):
            if asset["status"] in ("generated", "pending"):
                state_manager.reject_asset(job_id, i)

        state_manager.update_job_status(job_id, "revising")
        await self.telegram.send_text(
            f"❌ All assets rejected for {job_id}\n"
            "Use /next to process the next reference, or regenerate individual assets."
        )

    async def _handle_regenerate(self, job_id: str, asset_index: int) -> None:
        """Regenerate a single asset."""
        job = state_manager.get_job(job_id)
        if asset_index >= len(job["assets"]):
            await self.telegram.send_text(f"Invalid asset index: {asset_index}")
            return

        asset = job["assets"][asset_index]
        prompt_text = asset.get("prompt", "")
        generator = asset.get("generator", "")
        content_type = job["content_type"]

        if not prompt_text:
            await self.telegram.send_text(f"No prompt for asset {asset_index}")
            return

        state_manager.update_asset(job_id, asset_index, status="generating")
        await self.telegram.send_text(f"🔄 Regenerating asset #{asset_index + 1}...")

        try:
            if generator == "z_image":
                img_bytes = z_image_generator.generate_image(prompt_text, content_type)
                file_path = z_image_generator.save_image(img_bytes, job_id, asset_index)
            elif generator == "nano_banana":
                img_bytes = nano_banana_generator.generate_image(prompt_text, content_type)
                file_path = nano_banana_generator.save_image(img_bytes, job_id, asset_index)
            else:
                raise Exception(f"Unknown generator: {generator}")

            state_manager.update_asset(job_id, asset_index, status="generated", file_path=file_path)

            # Re-send preview
            state_manager.update_job_status(job_id, "review")
            job = state_manager.get_job(job_id)
            await self.telegram.send_job_preview(
                job_id=job_id,
                content_type=content_type,
                assets=job["assets"],
            )

        except Exception as e:
            state_manager.update_asset(job_id, asset_index, status="failed")
            await self.telegram.send_text(f"❌ Regeneration failed: {e}")

    async def _handle_approve_text(self, job_id: str) -> None:
        """Approve text and finalize the job."""
        state_manager.update_job_status(job_id, "approved")
        job = state_manager.get_job(job_id)
        state_manager.save_version_snapshot(job_id)

        await self.telegram.send_text(
            f"🎉 <b>Job approved!</b>\n\n"
            f"<code>{job_id}</code>\n"
            f"Type: {job['content_type']}\n"
            f"Assets: {len(job['assets'])}\n\n"
            f"Content is ready. Files saved in job directory.",
        )

    async def _handle_regenerate_text(self, job_id: str) -> None:
        """Regenerate text."""
        job = state_manager.get_job(job_id)
        plan = job.get("plan", {})
        analysis = job.get("analysis", {})
        content_type = job["content_type"]

        await self.telegram.send_text("🔄 Regenerating text...")

        try:
            if content_type == "post":
                text_data = publish.generate_post_text(plan, analysis)
            elif content_type == "story":
                text_data = publish.generate_story_text(plan)
            elif content_type == "reel":
                text_data = publish.generate_reel_text(plan)
            else:
                text_data = {"caption": "", "hashtags": []}

            state_manager.set_text(job_id, text_data)
            await self.telegram.send_text_review(job_id, text_data)

        except Exception as e:
            await self.telegram.send_text(f"❌ Text regeneration failed: {e}")

    async def _handle_approve_frame(self, job_id: str) -> None:
        """Approve start frame and launch Kling render."""
        state_manager.approve_asset(job_id, 0)
        await self.telegram.send_text(
            "✅ Start frame approved!\n⚙️ Starting Kling render... This may take 3-5 minutes."
        )

        job = state_manager.get_job(job_id)
        state_manager.update_job_status(job_id, "generating")

        try:
            # Find motion reference (from downloaded references)
            ref_dir = Path(state_manager._job_dir(job_id)) / "references"
            motion_ref = None
            for f in ref_dir.iterdir():
                if f.suffix in (".mp4", ".mov", ".webm"):
                    motion_ref = str(f)
                    break

            start_frame = job["assets"][0].get("file_path", "")

            if not motion_ref:
                raise Exception("No motion reference video found in job references")
            if not start_frame:
                raise Exception("No start frame file found")

            # Generate video with Kling
            video_bytes, video_url = kling_generator.motion_control(
                motion_ref, start_frame,
            )
            file_path = kling_generator.save_video(video_bytes, job_id, 2)

            # Update final_render asset
            if len(job["assets"]) > 2:
                state_manager.update_asset(job_id, 2, status="generated", file_path=file_path)

            # Send for review
            state_manager.update_job_status(job_id, "review")
            await self.telegram.send_video(file_path, caption=f"🎬 Reel preview\n<code>{job_id}</code>")

            job = state_manager.get_job(job_id)
            await self.telegram.send_job_preview(
                job_id=job_id,
                content_type="reel",
                assets=job["assets"],
            )

        except Exception as e:
            state_manager.update_job_status(job_id, "failed", error=str(e))
            await self.telegram.send_text(f"❌ Kling render failed: {e}")
            traceback.print_exc()

    async def _handle_regenerate_frame(self, job_id: str) -> None:
        """Regenerate the reel start frame."""
        await self._handle_regenerate(job_id, 0)

    # ── Helpers ──────────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        """Check if message is from authorized chat."""
        chat_id = str(update.effective_chat.id)
        return chat_id == str(TELEGRAM_CHAT_ID)


def show_status() -> None:
    """Print status of all jobs to stdout."""
    jobs = state_manager.list_jobs()
    if not jobs:
        print("No jobs.")
        return

    for j in jobs:
        print(f"  [{j['status']:12s}] {j['job_id']} ({j['content_type']})")
        for a in j.get("assets", []):
            print(f"    {a['index']+1}. {a['role']:12s} | {a['generator']:12s} | {a['status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MC Conductor — Content Creation Orchestrator")
    parser.add_argument("--status", action="store_true", help="Show all jobs")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    asyncio.run(MCConductor().start())


if __name__ == "__main__":
    main()
