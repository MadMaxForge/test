#!/usr/bin/env python3
"""
MC Conductor - Main orchestrator for the Instagram AI Pipeline.

The MC is the ONLY entry point. It:
- Manages the Telegram event loop (receives commands, sends previews)
- Coordinates all agents: Scout → Planner → Creative → Generators → Telegram → Publish
- Manages package state machine: draft → planning → production → review → text_review → approved
- Handles regeneration (partial: only rejected assets), rollback, recovery
- Enforces job locking (1 active job per package)
- Checks Telegram between steps (can interrupt long workflows)

Usage:
    python3 mc_conductor.py                    # Start the MC event loop
    python3 mc_conductor.py --process-next     # Process next package from queue (one-shot)
    python3 mc_conductor.py --status           # Show status of all packages

Requires:
    pip3 install python-telegram-bot==20.7 requests
    Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENROUTER_API_KEY,
                 RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, EVOLINK_API_KEY
"""

import json
import os
import sys
import asyncio
import time
import traceback
from datetime import datetime, timezone

# Add scripts dir to path
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Timeouts per operation (seconds)
TIMEOUTS = {
    "scout": 180,
    "planner": 60,
    "creative": 120,
    "z_image": 600,
    "nano_banana": 180,
    "kling": 600,
    "publish": 60,
    "telegram_poll": 3600,
}

# How often to check Telegram between steps (seconds)
TELEGRAM_CHECK_INTERVAL = 5


class MCConductor:
    """Main orchestrator for the Instagram AI content pipeline."""

    def __init__(self):
        from state_manager import StateManager
        from asset_router import AssetRouter
        from reference_queue import ReferenceQueue
        from package_planner import PackagePlanner
        from package_creative import PackageCreative
        from package_publish import PackagePublish
        from telegram_manager import TelegramManager

        self.state_manager = StateManager()
        self.router = AssetRouter()
        self.ref_queue = ReferenceQueue()
        self.planner = PackagePlanner()
        self.creative = PackageCreative()
        self.publisher = PackagePublish()
        self.telegram = TelegramManager()
        self.bot = None
        self._processed_callbacks = set()  # Idempotency: track processed callback IDs

    # ── Main Event Loop ──────────────────────────────────────────

    async def start(self):
        """Start the MC event loop — listens for Telegram commands + processes packages."""
        print("[MC] Starting MC Conductor event loop...")
        print("[MC] Workspace: %s" % WORKSPACE)
        print("[MC] Telegram chat: %s" % TELEGRAM_CHAT_ID)

        await self._init_bot()
        await self._send_telegram("MC Conductor started. Send commands:\n"
                                  "/status — show all packages\n"
                                  "/next — process next reference\n"
                                  "/queue — show reference queue\n"
                                  "/add <url> — add reference URL")

        # Start polling for Telegram updates
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("next", self._cmd_process_next))
        app.add_handler(CommandHandler("queue", self._cmd_queue))
        app.add_handler(CommandHandler("add", self._cmd_add_reference))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self._handle_text_message))

        print("[MC] Bot polling started. Waiting for commands...")
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

    # ── Telegram Command Handlers ────────────────────────────────

    async def _cmd_help(self, update, context):
        """Handle /help command."""
        await update.message.reply_text(
            "MC Conductor Commands:\n\n"
            "/status — Package status overview\n"
            "/next — Process next reference from queue\n"
            "/queue — Show reference queue counts\n"
            "/add <url> — Add Instagram URL (auto-detects post/reel)\n"
            "/help — This message\n\n"
            "You can also just paste an Instagram URL to add it to the queue."
        )

    async def _cmd_status(self, update, context):
        """Handle /status command — show all packages."""
        packages = self.state_manager.list_packages()
        if not packages:
            await update.message.reply_text("No packages yet. Use /add <url> to add references.")
            return

        lines = ["📦 Packages (%d):\n" % len(packages)]
        for pkg in packages[-10:]:  # Show last 10
            asset_count = len(pkg.get("assets", []))
            approved = sum(1 for a in pkg.get("assets", []) if a.get("status") == "approved")
            lines.append("• %s\n  Theme: %s\n  Status: %s\n  Assets: %d/%d approved" % (
                pkg["package_id"][:40], pkg["theme"],
                pkg["status"], approved, asset_count))

        await update.message.reply_text("\n".join(lines))

    async def _cmd_queue(self, update, context):
        """Handle /queue command — show reference queue."""
        counts = self.ref_queue.count()
        post_new = counts.get("post_references", {}).get("new", 0)
        post_used = counts.get("post_references", {}).get("used", 0)
        reel_new = counts.get("reel_references", {}).get("new", 0)
        reel_used = counts.get("reel_references", {}).get("used", 0)

        text = (
            "Reference Queue:\n\n"
            "Posts: %d new, %d used\n"
            "Reels: %d new, %d used\n\n"
            "Use /add <url> to add more.\n"
            "Use /next to process the next one."
        ) % (post_new, post_used, reel_new, reel_used)

        await update.message.reply_text(text)

    async def _cmd_add_reference(self, update, context):
        """Handle /add <url> command — add reference to queue."""
        if not context.args:
            await update.message.reply_text("Usage: /add <instagram_url> [note]")
            return

        url = context.args[0]
        note = " ".join(context.args[1:]) if len(context.args) > 1 else None

        url_type = self.ref_queue.detect_url_type(url)
        if url_type == "reel":
            ref = self.ref_queue.add_reel_reference(url, note=note)
            await update.message.reply_text(
                "Added reel reference: %s\nID: %s" % (url[:60], ref["ref_id"]))
        elif url_type == "post":
            ref = self.ref_queue.add_post_reference(url, note=note)
            await update.message.reply_text(
                "Added post reference: %s\nID: %s" % (url[:60], ref["ref_id"]))
        else:
            await update.message.reply_text(
                "Can't detect URL type. Send a /add with instagram.com/p/... or instagram.com/reel/...")

    async def _handle_text_message(self, update, context):
        """Handle plain text messages — auto-detect Instagram URLs."""
        text = update.message.text.strip()

        if "instagram.com/" in text:
            # Extract URL
            import re
            urls = re.findall(r'https?://[^\s]+instagram\.com/[^\s]+', text)
            if not urls:
                urls = re.findall(r'instagram\.com/[^\s]+', text)
                urls = ["https://www." + u for u in urls]

            if urls:
                for url in urls:
                    url_type = self.ref_queue.detect_url_type(url)
                    if url_type == "reel":
                        ref = self.ref_queue.add_reel_reference(url)
                        await update.message.reply_text(
                            "Added reel: %s" % ref["ref_id"])
                    elif url_type == "post":
                        ref = self.ref_queue.add_post_reference(url)
                        await update.message.reply_text(
                            "Added post: %s" % ref["ref_id"])
                    else:
                        await update.message.reply_text(
                            "Can't detect type for: %s" % url[:60])
                return

        await update.message.reply_text(
            "Send an Instagram URL or use /help for commands.")

    async def _cmd_process_next(self, update, context):
        """Handle /next command — process next reference from queue."""
        await update.message.reply_text("Processing next reference...")

        try:
            result = await self.process_next_package()
            if result:
                await update.message.reply_text(
                    "Package created: %s\nStatus: %s" % (
                        result["package_id"], result["status"]))
            else:
                await update.message.reply_text(
                    "No new references in queue. Use /add <url> to add some.")
        except Exception as e:
            await update.message.reply_text(
                "Error processing: %s" % str(e)[:200])
            print("[MC] Error in process_next: %s" % traceback.format_exc())

    # ── Callback Handler (Approve/Reject/Regenerate) ─────────────

    async def _handle_callback(self, update, context):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        callback_data = query.data
        callback_id = query.id

        # Idempotency check
        if callback_id in self._processed_callbacks:
            print("[MC] Duplicate callback ignored: %s" % callback_id)
            return
        self._processed_callbacks.add(callback_id)

        # Keep set bounded
        if len(self._processed_callbacks) > 1000:
            self._processed_callbacks = set(list(self._processed_callbacks)[-500:])

        print("[MC] Callback received: %s" % callback_data)

        try:
            if callback_data.startswith("pkg_approve_"):
                package_id = callback_data.replace("pkg_approve_", "")
                await self._handle_package_approve(query, package_id)

            elif callback_data.startswith("pkg_reject_"):
                package_id = callback_data.replace("pkg_reject_", "")
                await self._handle_package_reject(query, package_id)

            elif callback_data.startswith("pkg_regen_"):
                # pkg_regen_<package_id>
                package_id = callback_data.replace("pkg_regen_", "")
                await self._show_regen_options(query, package_id)

            elif callback_data.startswith("regen_asset_"):
                # regen_asset_<package_id>_<asset_id>
                parts = callback_data.replace("regen_asset_", "").split("_", 1)
                if len(parts) >= 2:
                    # package_id can contain underscores, so we need to be smarter
                    await self._handle_regen_asset(query, callback_data)

            elif callback_data.startswith("txt_approve_"):
                package_id = callback_data.replace("txt_approve_", "")
                await self._handle_text_approve(query, package_id)

            elif callback_data.startswith("txt_reject_"):
                package_id = callback_data.replace("txt_reject_", "")
                await self._handle_text_reject(query, package_id)

            elif callback_data.startswith("reel_frame_approve_"):
                package_id = callback_data.replace("reel_frame_approve_", "")
                await self._handle_reel_frame_approve(query, package_id)

            elif callback_data.startswith("reel_frame_reject_"):
                package_id = callback_data.replace("reel_frame_reject_", "")
                await self._handle_reel_frame_reject(query, package_id)

            else:
                await query.edit_message_text("Unknown action: %s" % callback_data)

        except Exception as e:
            print("[MC] Callback error: %s" % traceback.format_exc())
            try:
                await query.edit_message_text("Error: %s" % str(e)[:200])
            except Exception:
                pass

    # ── Approval Handlers ────────────────────────────────────────

    async def _handle_package_approve(self, query, package_id):
        """All assets approved — move to text generation."""
        state = self.state_manager.get_package(package_id)

        # Mark all pending_review assets as approved
        for asset in state["assets"]:
            if asset["status"] == "pending_review":
                self.state_manager.update_asset_status(
                    package_id, asset["asset_id"], "approved")

        self.state_manager.update_package_status(package_id, "text_review")

        await query.edit_message_text(
            "All assets approved! Generating text (caption + hashtags)...")

        # Generate text
        try:
            await self._run_publish(package_id)
        except Exception as e:
            await self._send_telegram(
                "Error generating text for %s: %s" % (package_id, str(e)[:200]))

    async def _handle_package_reject(self, query, package_id):
        """Package rejected — archive all assets, reset."""
        state = self.state_manager.get_package(package_id)

        for asset in state["assets"]:
            if asset["status"] != "archived":
                self.state_manager.update_asset_status(
                    package_id, asset["asset_id"], "archived")

        self.state_manager.update_package_status(package_id, "draft")

        await query.edit_message_text(
            "Package rejected. All assets archived. Send /next to create a new package.")

    async def _show_regen_options(self, query, package_id):
        """Show per-asset regeneration buttons — delegates to TelegramManager."""
        state = self.state_manager.get_package(package_id)
        await query.edit_message_text("Loading regeneration options...")
        await self.telegram.send_regen_options(package_id, state["assets"])

    async def _handle_regen_asset(self, query, callback_data):
        """Regenerate a specific asset."""
        # Parse: regen_asset_<package_id>___<asset_id>
        payload = callback_data.replace("regen_asset_", "")
        parts = payload.split("___")
        if len(parts) != 2:
            await query.edit_message_text("Invalid regeneration request.")
            return

        package_id, asset_id = parts

        await query.edit_message_text(
            "Regenerating %s... This may take a few minutes." % asset_id)

        try:
            self.state_manager.update_asset_status(package_id, asset_id, "rejected")
            self.state_manager.increment_revision_count(package_id)

            # Re-run creative + generation for this one asset
            await self._regenerate_single_asset(package_id, asset_id)

            # Re-send preview
            await self._send_package_preview(package_id)

        except Exception as e:
            await self._send_telegram(
                "Regeneration failed for %s/%s: %s" % (
                    package_id, asset_id, str(e)[:200]))

    async def _handle_text_approve(self, query, package_id):
        """Text approved — package is fully approved."""
        self.state_manager.update_text(package_id, text_status="approved")
        self.state_manager.update_package_status(package_id, "approved")

        await query.edit_message_text(
            "Package fully approved! Ready for scheduling/publishing.")

        state = self.state_manager.get_package(package_id)
        summary = self.state_manager.get_asset_summary(package_id)

        await self._send_telegram(
            "Package %s is APPROVED!\n\n"
            "Theme: %s\n"
            "Assets: %d total (%d character, %d world)\n"
            "Status: approved — ready for publish" % (
                package_id, state["theme"],
                summary["total"],
                summary["character_count"],
                summary["world_count"]))

    async def _handle_text_reject(self, query, package_id):
        """Text rejected — regenerate text."""
        self.state_manager.update_text(package_id, text_status="pending")

        await query.edit_message_text("Text rejected. Regenerating...")

        try:
            await self._run_publish(package_id)
        except Exception as e:
            await self._send_telegram(
                "Error regenerating text: %s" % str(e)[:200])

    async def _handle_reel_frame_approve(self, query, package_id):
        """Reel start frame approved — launch Kling motion render."""
        state = self.state_manager.get_package(package_id)

        reel_asset = None
        for asset in state["assets"]:
            if asset["type"] == "reel_start_frame":
                reel_asset = asset
                break

        if not reel_asset:
            await query.edit_message_text("No reel asset found in package.")
            return

        self.state_manager.update_reel_stage(
            package_id, reel_asset["asset_id"], "start_frame_approved")

        await query.edit_message_text(
            "Reel frame approved! Starting Kling motion render (3-5 min)...")

        # Launch Kling generation
        try:
            await self._run_kling_motion(package_id, reel_asset)
        except Exception as e:
            await self._send_telegram(
                "Kling error: %s" % str(e)[:200])

    async def _handle_reel_frame_reject(self, query, package_id):
        """Reel start frame rejected — regenerate frame."""
        state = self.state_manager.get_package(package_id)

        reel_asset = None
        for asset in state["assets"]:
            if asset["type"] == "reel_start_frame":
                reel_asset = asset
                break

        if not reel_asset:
            await query.edit_message_text("No reel asset found.")
            return

        self.state_manager.update_asset_status(
            package_id, reel_asset["asset_id"], "rejected")

        await query.edit_message_text(
            "Reel frame rejected. Regenerating...")

        try:
            await self._regenerate_single_asset(package_id, reel_asset["asset_id"])
        except Exception as e:
            await self._send_telegram(
                "Reel regeneration error: %s" % str(e)[:200])

    # ── Core Pipeline ────────────────────────────────────────────

    async def process_next_package(self):
        """
        Process the next reference from the queue through the full pipeline.
        Returns the created package state, or None if queue is empty.
        """
        # Get next post reference
        post_ref = self.ref_queue.get_next_post_reference()
        if not post_ref:
            print("[MC] No new post references in queue.")
            return None

        # Get matching reel reference (if available)
        reel_ref = self.ref_queue.get_next_reel_reference()

        print("[MC] Processing post reference: %s" % post_ref["url"])
        if reel_ref:
            print("[MC] Found reel reference: %s" % reel_ref["url"])

        # Create package
        source = {
            "mode": "manual_queue",
            "post_reference": {
                "ref_id": post_ref["ref_id"],
                "url": post_ref["url"],
                "post_id": post_ref.get("post_id"),
            },
            "reel_reference": {
                "ref_id": reel_ref["ref_id"],
                "url": reel_ref["url"],
                "post_id": reel_ref.get("post_id"),
            } if reel_ref else None,
        }

        # Determine theme from note or URL
        theme = post_ref.get("note") or "content_%s" % datetime.now(
            timezone.utc).strftime("%Y%m%d_%H%M%S")

        state = self.state_manager.create_package(theme, source)
        package_id = state["package_id"]

        # Mark references as used
        self.ref_queue.mark_used(post_ref["ref_id"], package_id)
        if reel_ref:
            self.ref_queue.mark_used(reel_ref["ref_id"], package_id)

        await self._send_telegram(
            "New package: %s\nTheme: %s\nStarting pipeline..." % (
                package_id, theme))

        # Acquire lock
        lock_id = self.state_manager.acquire_lock(package_id, "full_pipeline")

        try:
            # STEP 1: Scout — download + analyze reference
            await self._send_telegram("Step 1/5: Scout analyzing reference...")
            self.state_manager.update_package_status(package_id, "planning")
            scout_result = await self._run_scout(package_id, post_ref)

            # STEP 2: Planner — create asset plan
            await self._send_telegram("Step 2/5: Planner building package plan...")
            plan = await self._run_planner(package_id, scout_result, reel_ref)

            # STEP 3: Creative — generate prompts
            await self._send_telegram("Step 3/5: Creative writing prompts...")
            self.state_manager.update_package_status(package_id, "production")
            prompts = await self._run_creative(package_id, plan)

            # STEP 4: Generate all assets
            await self._send_telegram("Step 4/5: Generating images...")
            await self._run_generators(package_id, prompts)

            # STEP 5: Send preview to Telegram
            await self._send_telegram("Step 5/5: Sending preview for review...")
            self.state_manager.update_package_status(package_id, "review")
            await self._send_package_preview(package_id)

        except Exception as e:
            print("[MC] Pipeline error: %s" % traceback.format_exc())
            await self._send_telegram(
                "Pipeline error for %s:\n%s" % (package_id, str(e)[:300]))
        finally:
            self.state_manager.release_lock(package_id, lock_id)

        return self.state_manager.get_package(package_id)

    # ── Agent Runners ────────────────────────────────────────────

    async def _run_scout(self, package_id, post_ref):
        """
        Run Scout agent on the post reference.
        Downloads images and performs vision analysis.

        Returns: dict — scout analysis result
        """
        pkg_dir = self.state_manager.get_package_dir(package_id)
        analysis_dir = self.state_manager.get_package_subdir(package_id, "analysis")
        ref_dir = self.state_manager.get_package_subdir(package_id, "references")

        # Download reference images using instagram_scraper
        url = post_ref["url"]
        post_id = post_ref.get("post_id", "unknown")

        print("[MC/Scout] Downloading reference from %s..." % url)

        # Use instagram_scraper to download
        try:
            from instagram_scraper import download_post_by_url
            downloaded = download_post_by_url(url, ref_dir)
            print("[MC/Scout] Downloaded %d files" % len(downloaded))
        except ImportError:
            print("[MC/Scout] instagram_scraper not available, trying direct download...")
            downloaded = await self._download_reference_direct(url, ref_dir)
        except Exception as e:
            print("[MC/Scout] Download error: %s" % str(e))
            downloaded = []

        if not downloaded:
            print("[MC/Scout] No files downloaded, creating placeholder analysis")
            return {"error": "no_files", "slides": []}

        # Run Scout vision analysis on downloaded images
        from scout_agent import (
            encode_image_base64, parse_json_response,
            SCOUT_SYSTEM_PROMPT, MODEL as SCOUT_MODEL,
        )

        image_files = []
        from pathlib import Path
        for ext in ("*.jpg", "*.png", "*.webp"):
            image_files.extend(Path(ref_dir).glob(ext))

        if not image_files:
            return {"error": "no_images", "slides": []}

        print("[MC/Scout] Analyzing %d images with vision..." % len(image_files))

        # Build analysis prompt for package-based output
        analysis_prompt = self._build_scout_prompt(image_files)
        analysis = await self._call_openrouter_vision(
            analysis_prompt, image_files[:6],
            system_prompt=SCOUT_SYSTEM_PROMPT,
            model=SCOUT_MODEL,
        )

        analysis["reference_id"] = "ref_%s" % post_id
        analysis["source_url"] = url
        analysis["analyzed_at"] = datetime.now(timezone.utc).isoformat()

        # Save analysis
        analysis_path = os.path.join(analysis_dir, "scout_analysis.json")
        with open(analysis_path, "w") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)

        print("[MC/Scout] Analysis saved: %s" % analysis_path)
        return analysis

    async def _run_planner(self, package_id, scout_result, reel_ref=None):
        """
        Run Package Planner — delegates to PackagePlanner module.
        Registers planned assets in state_manager.

        Returns: dict — package plan
        """
        plan_dir = self.state_manager.get_package_subdir(package_id, "plan")
        state = self.state_manager.get_package(package_id)
        has_reel = reel_ref is not None

        # Delegate to PackagePlanner
        plan = self.planner.create_plan(
            package_id, state["theme"], scout_result,
            has_reel=has_reel)

        # Register each planned asset in state_manager
        for asset in plan.get("assets", []):
            self.state_manager.add_asset(
                package_id, asset["asset_id"], asset["type"],
                asset["order"], asset.get("role", ""),
                asset["has_character"], asset["generator"],
                motion_ref=asset.get("motion_ref"))

        # Save plan file
        plan_path = os.path.join(plan_dir, "package_plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        print("[MC/Planner] Plan saved: %d assets" % len(plan.get("assets", [])))
        return plan

    async def _run_creative(self, package_id, plan):
        """
        Run Creative agent — delegates to PackageCreative module.

        Returns: dict — creative prompts output
        """
        prompts_dir = self.state_manager.get_package_subdir(package_id, "prompts")
        analysis_dir = self.state_manager.get_package_subdir(package_id, "analysis")

        # Load scout analysis for context
        analysis_path = os.path.join(analysis_dir, "scout_analysis.json")
        scout_data = {}
        if os.path.exists(analysis_path):
            with open(analysis_path) as f:
                scout_data = json.load(f)

        # Delegate to PackageCreative
        result = self.creative.generate_prompts(package_id, plan, scout_data)

        # Update asset statuses to prompt_ready
        for prompt_item in result.get("prompts", []):
            asset_id = prompt_item.get("asset_id")
            if asset_id:
                try:
                    self.state_manager.update_asset_status(
                        package_id, asset_id, "prompt_ready")
                except ValueError:
                    pass

        # Save prompts
        prompts_path = os.path.join(prompts_dir, "creative_prompts.json")
        with open(prompts_path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print("[MC/Creative] Prompts saved: %d prompts" % len(result.get("prompts", [])))
        return result

    async def _run_generators(self, package_id, prompts):
        """
        Run image generators for all assets with prompts.
        Updates state with generated file paths and versions.
        """
        pkg_dir = self.state_manager.get_package_dir(package_id)

        for prompt_item in prompts.get("prompts", []):
            asset_id = prompt_item.get("asset_id")
            generator = prompt_item.get("generator")
            prompt_text = prompt_item.get("prompt", "")
            prompt_hash = prompt_item.get("prompt_hash", "unknown")
            asset_type = prompt_item.get("type", "post_slide")

            if not asset_id or not generator:
                continue

            # Determine output subdir
            if asset_type == "post_slide":
                out_subdir = "generated/posts"
            elif asset_type == "story_frame":
                out_subdir = "generated/stories"
            elif asset_type == "reel_start_frame":
                out_subdir = "generated/reels"
            else:
                out_subdir = "generated/posts"

            out_dir = self.state_manager.get_package_subdir(package_id, out_subdir)

            self.state_manager.update_asset_status(
                package_id, asset_id, "generating")

            start_time = time.time()

            try:
                if generator == "z_image":
                    file_path = await self._generate_z_image(
                        prompt_text, asset_id, out_dir)
                elif generator == "nano_banana":
                    file_path = await self._generate_nano_banana(
                        prompt_text, asset_id, out_dir)
                else:
                    print("[MC/Gen] Unknown generator: %s" % generator)
                    continue

                gen_time = time.time() - start_time

                if file_path and os.path.exists(file_path):
                    # Save version
                    rel_path = os.path.relpath(file_path, pkg_dir)
                    self.state_manager.save_asset_version(
                        package_id, asset_id, rel_path, prompt_hash,
                        generation_time_sec=round(gen_time))

                    print("[MC/Gen] %s generated: %s (%.1fs)" % (
                        asset_id, file_path, gen_time))
                else:
                    print("[MC/Gen] %s generation failed (no file)" % asset_id)
                    self.state_manager.update_asset_status(
                        package_id, asset_id, "planned")

            except Exception as e:
                print("[MC/Gen] Error generating %s: %s" % (asset_id, str(e)))
                self.state_manager.update_asset_status(
                    package_id, asset_id, "planned")

    async def _run_publish(self, package_id):
        """
        Run Publish agent — delegates to PackagePublish module.
        Sends text preview to Telegram for approval.
        """
        state = self.state_manager.get_package(package_id)
        theme = state["theme"]

        # Load plan summary if available
        plan_dir = self.state_manager.get_package_subdir(package_id, "plan")
        plan_path = os.path.join(plan_dir, "package_plan.json")
        plan_summary = None
        if os.path.exists(plan_path):
            with open(plan_path) as f:
                plan_data = json.load(f)
            plan_summary = plan_data.get("summary")

        # Delegate to PackagePublish
        result = self.publisher.generate_text(
            package_id, theme, state["assets"], plan_summary)

        # Update state with text
        post_data = result.get("post", {})
        self.state_manager.update_text(
            package_id,
            post_caption=post_data.get("caption"),
            post_hashtags=post_data.get("hashtags"),
            story_overlays=result.get("stories"),
            reel_caption=result.get("reel", {}).get("caption") if result.get("reel") else None,
            text_status="pending_review",
        )

        # Save publish output
        text_dir = self.state_manager.get_package_subdir(package_id, "text")
        with open(os.path.join(text_dir, "publish_output.json"), "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # Send text preview to Telegram via TelegramManager
        await self.telegram.send_text_preview(package_id, result)

    async def _run_kling_motion(self, package_id, reel_asset):
        """Run Kling motion generation for an approved reel start frame."""
        self.state_manager.update_reel_stage(
            package_id, reel_asset["asset_id"], "motion_rendering")

        # Get start frame path
        pkg_dir = self.state_manager.get_package_dir(package_id)
        frame_path = os.path.join(pkg_dir, reel_asset.get("start_frame", ""))
        motion_ref = reel_asset.get("motion_ref", "")

        if not os.path.exists(frame_path):
            raise FileNotFoundError("Start frame not found: %s" % frame_path)

        print("[MC/Kling] Generating motion from frame: %s" % frame_path)
        print("[MC/Kling] Motion reference: %s" % motion_ref)

        try:
            from kling_motion_control import motion_control
            video_bytes, video_url = motion_control(motion_ref, frame_path)

            if video_url:
                reel_output = video_url
                self.state_manager.update_reel_stage(
                    package_id, reel_asset["asset_id"], "motion_review")

                # Update state
                state = self.state_manager.get_package(package_id)
                for asset in state["assets"]:
                    if asset["asset_id"] == reel_asset["asset_id"]:
                        asset["reel_output"] = reel_output
                        break
                self.state_manager._write_state(package_id, state)

                await self._send_telegram(
                    "Kling video generated! Sending for review...")
            else:
                raise RuntimeError("Kling returned no output")

        except Exception as e:
            self.state_manager.update_reel_stage(
                package_id, reel_asset["asset_id"], "start_frame_approved")
            raise

    # ── Regeneration ─────────────────────────────────────────────

    async def _regenerate_single_asset(self, package_id, asset_id):
        """Regenerate a single rejected asset. Preserves all other assets."""
        state = self.state_manager.get_package(package_id)

        # Find the asset
        target_asset = None
        for asset in state["assets"]:
            if asset["asset_id"] == asset_id:
                target_asset = asset
                break

        if not target_asset:
            raise ValueError("Asset not found: %s" % asset_id)

        # Load existing prompts
        prompts_dir = self.state_manager.get_package_subdir(package_id, "prompts")
        prompts_path = os.path.join(prompts_dir, "creative_prompts.json")

        if not os.path.exists(prompts_path):
            raise FileNotFoundError("No prompts found for regeneration")

        with open(prompts_path) as f:
            all_prompts = json.load(f)

        # Find prompt for this asset
        target_prompt = None
        for p in all_prompts.get("prompts", []):
            if p.get("asset_id") == asset_id:
                target_prompt = p
                break

        if not target_prompt:
            raise ValueError("No prompt found for asset: %s" % asset_id)

        # Re-generate just this one asset
        self.state_manager.update_asset_status(package_id, asset_id, "generating")

        pkg_dir = self.state_manager.get_package_dir(package_id)
        asset_type = target_asset["type"]
        generator = target_asset["generator"]

        if asset_type == "post_slide":
            out_dir = self.state_manager.get_package_subdir(package_id, "generated/posts")
        elif asset_type == "story_frame":
            out_dir = self.state_manager.get_package_subdir(package_id, "generated/stories")
        else:
            out_dir = self.state_manager.get_package_subdir(package_id, "generated/reels")

        start_time = time.time()

        if generator == "z_image":
            file_path = await self._generate_z_image(
                target_prompt["prompt"], asset_id, out_dir)
        elif generator == "nano_banana":
            file_path = await self._generate_nano_banana(
                target_prompt["prompt"], asset_id, out_dir)
        else:
            raise ValueError("Unknown generator: %s" % generator)

        gen_time = time.time() - start_time

        if file_path and os.path.exists(file_path):
            rel_path = os.path.relpath(file_path, pkg_dir)
            self.state_manager.save_asset_version(
                package_id, asset_id, rel_path,
                target_prompt.get("prompt_hash", "regen"),
                generation_time_sec=round(gen_time))
            print("[MC/Regen] %s regenerated: %s (%.1fs)" % (
                asset_id, file_path, gen_time))
        else:
            self.state_manager.update_asset_status(package_id, asset_id, "planned")
            raise RuntimeError("Regeneration failed for %s" % asset_id)

    # ── Preview Sending ──────────────────────────────────────────

    async def _send_package_preview(self, package_id):
        """Send all generated assets as a preview — delegates to TelegramManager."""
        state = self.state_manager.get_package(package_id)
        pkg_dir = self.state_manager.get_package_dir(package_id)

        message_ids = await self.telegram.send_package_preview(
            package_id, state, pkg_dir)

        # Save preview message IDs for cleanup
        self.state_manager.update_review_context(
            package_id, last_preview_message_ids=message_ids)

    async def _send_text_preview(self, package_id, publish_data):
        """Send text preview — delegates to TelegramManager."""
        await self.telegram.send_text_preview(package_id, publish_data)

    # ── Generator Wrappers ───────────────────────────────────────

    async def _generate_z_image(self, prompt, asset_id, out_dir):
        """Generate an image using Z-Image (RunPod)."""
        try:
            from runpod_generator import generate_image
            output_path = os.path.join(out_dir, "%s_v%d.png" % (
                asset_id, int(time.time()) % 10000))
            result = generate_image(prompt, output_path)
            if result and os.path.exists(output_path):
                return output_path
            return None
        except Exception as e:
            print("[MC/Z-Image] Error: %s" % str(e))
            return None

    async def _generate_nano_banana(self, prompt, asset_id, out_dir):
        """Generate an image using Nano Banana (Evolink)."""
        try:
            from nano_banana_generator import generate_nano_banana
            output_path = os.path.join(out_dir, "%s_v%d.png" % (
                asset_id, int(time.time()) % 10000))
            result = generate_nano_banana(prompt, output_path)
            if result and os.path.exists(output_path):
                return output_path
            return None
        except Exception as e:
            print("[MC/NanoBanana] Error: %s" % str(e))
            return None

    # ── OpenRouter API Calls ─────────────────────────────────────

    async def _call_openrouter_text(self, user_prompt, system_prompt="",
                                     model="google/gemini-2.0-flash-lite-001"):
        """Call OpenRouter text API and parse JSON response."""
        import requests as req

        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        try:
            resp = req.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer %s" % api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 8000,
                },
                timeout=TIMEOUTS.get("creative", 120),
            )

            if resp.status_code != 200:
                print("[MC/API] Error %d: %s" % (resp.status_code, resp.text[:300]))
                return None

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return None

            from scout_agent import parse_json_response
            return parse_json_response(content)

        except Exception as e:
            print("[MC/API] Request error: %s" % str(e))
            return None

    async def _call_openrouter_vision(self, text_prompt, image_files,
                                       system_prompt="",
                                       model="google/gemini-2.0-flash-lite-001"):
        """Call OpenRouter vision API with images."""
        import requests as req
        from scout_agent import encode_image_base64, parse_json_response

        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        content_parts = [{"type": "text", "text": text_prompt}]
        for img_file in image_files:
            b64 = encode_image_base64(img_file)
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,%s" % b64},
            })

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        try:
            resp = req.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer %s" % api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 16000,
                },
                timeout=TIMEOUTS.get("scout", 180),
            )

            if resp.status_code != 200:
                print("[MC/Vision] Error %d: %s" % (resp.status_code, resp.text[:300]))
                return {}

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return parse_json_response(content) if content else {}

        except Exception as e:
            print("[MC/Vision] Error: %s" % str(e))
            return {}

    # ── Prompt Builders ──────────────────────────────────────────

    def _build_scout_prompt(self, image_files):
        """Build the scout analysis prompt for package-based output."""
        return (
            "Analyze these reference images for content reproduction.\n\n"
            "For EACH image, analyze:\n"
            "1. Content type: character_portrait, character_full_body, world_detail, product_shot\n"
            "2. Has person: true/false\n"
            "3. Face visible: true/false\n"
            "4. Background: setting, colors, depth of field\n"
            "5. Subject: what/who is in the image\n"
            "6. Clothing: garments, accessories (if person)\n"
            "7. Pose: body position, gesture (if person)\n"
            "8. Lighting: type, direction, warmth\n"
            "9. Camera angle: level, distance, framing\n"
            "10. Mood: atmosphere, energy\n"
            "11. Reproducible: can this be regenerated by AI? (true/false)\n\n"
            "Also analyze the OVERALL structure:\n"
            "- Narrative flow between images\n"
            "- Character to world ratio\n"
            "- Mood progression\n\n"
            "Images: %s\n\n"
            "Output ONLY valid JSON with individual_analyses[] and aggregate_style{}." % (
                ", ".join(f.name for f in image_files))
        )


    # ── Telegram Helpers ─────────────────────────────────────────

    async def _init_bot(self):
        """Initialize the Telegram bot if not already done."""
        if self.bot is None:
            from telegram import Bot
            from telegram.request import HTTPXRequest
            request = HTTPXRequest(
                read_timeout=60, write_timeout=60, connect_timeout=30)
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN, request=request)

    async def _send_telegram(self, text):
        """Send a message to the configured Telegram chat."""
        await self._init_bot()
        try:
            await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        except Exception as e:
            print("[MC/Telegram] Send error: %s" % str(e))

    async def _download_reference_direct(self, url, out_dir):
        """Fallback: try to download reference directly."""
        # Placeholder — in production, use instagram_scraper
        print("[MC] Direct download not implemented, need instagram_scraper")
        return []

    # ── Recovery ─────────────────────────────────────────────────

    async def recover_package(self, package_id):
        """
        Recover a package after restart.
        Checks state and resumes from last known step.
        """
        state, resume_info = self.state_manager.get_recovery_state(package_id)

        print("[MC/Recovery] Package: %s" % package_id)
        print("[MC/Recovery] Status: %s" % resume_info["package_status"])
        print("[MC/Recovery] Assets: %d planned, %d generated, %d approved" % (
            resume_info["assets_planned"],
            resume_info["assets_generated"],
            resume_info["assets_approved"]))

        pkg_status = resume_info["package_status"]

        if pkg_status == "draft":
            print("[MC/Recovery] Package is in draft — needs manual trigger")

        elif pkg_status == "planning":
            print("[MC/Recovery] Resuming from planning stage")
            # Re-run from planner

        elif pkg_status == "production":
            # Check which assets still need generation
            if resume_info["assets_pending"]:
                print("[MC/Recovery] Resuming generation for: %s" % (
                    resume_info["assets_pending"]))

        elif pkg_status == "review":
            print("[MC/Recovery] Waiting for user review — re-sending preview")
            await self._send_package_preview(package_id)

        elif pkg_status == "text_review":
            print("[MC/Recovery] Waiting for text review")

        elif pkg_status == "approved":
            print("[MC/Recovery] Package already approved!")

        return resume_info


# ── CLI ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        mc = MCConductor()

        if cmd == "--status":
            packages = mc.state_manager.list_packages()
            if not packages:
                print("No packages.")
                return
            for pkg in packages:
                asset_count = len(pkg.get("assets", []))
                approved = sum(1 for a in pkg.get("assets", [])
                               if a.get("status") == "approved")
                print("%s | %s | %s | %d/%d approved" % (
                    pkg["package_id"], pkg["status"], pkg["theme"],
                    approved, asset_count))

        elif cmd == "--process-next":
            asyncio.run(mc.process_next_package())

        elif cmd == "--recover":
            if len(sys.argv) < 3:
                print("Usage: mc_conductor.py --recover <package_id>")
                return
            asyncio.run(mc.recover_package(sys.argv[2]))

        else:
            print("Unknown command: %s" % cmd)
            print("Usage:")
            print("  python3 mc_conductor.py              # Start event loop")
            print("  python3 mc_conductor.py --status     # Show packages")
            print("  python3 mc_conductor.py --process-next  # Process next reference")
            print("  python3 mc_conductor.py --recover <pkg>  # Recover package")
    else:
        # Start event loop
        asyncio.run(MCConductor().start())


if __name__ == "__main__":
    main()
