"""Orchestrator — central coordinator for the Instagram agent system.

Manages the full pipeline:
  Research Agent -> Creative Agent -> Publish Agent -> Telegram Approval -> Post

Includes rate limiting, error handling, and task queue management.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.agents import brand_guide as bg
from app.agents import research_agent, creative_agent, publish_agent
from app.agents import telegram_bot, rate_limiter

logger = logging.getLogger(__name__)

# In-memory task store
_tasks: dict[str, dict] = {}


class PipelineTask:
    """Represents a single content generation pipeline run."""

    def __init__(
        self,
        task_id: str,
        character: str,
        reference_username: Optional[str] = None,
        num_images: int = 4,
    ):
        self.task_id = task_id
        self.character = character
        self.reference_username = reference_username
        self.num_images = num_images
        self.status = "created"
        self.current_step = ""
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.result: Optional[dict] = None
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "character": self.character,
            "reference_username": self.reference_username,
            "num_images": self.num_images,
            "status": self.status,
            "current_step": self.current_step,
            "created_at": self.created_at,
            "result": self.result,
            "error": self.error,
        }


def create_task(
    character: str,
    reference_username: Optional[str] = None,
    num_images: int = 4,
) -> PipelineTask:
    """Create a new pipeline task.

    Args:
        character: Brand guide character name (e.g. 'lanna_danger').
        reference_username: Instagram username to use as reference.
        num_images: Number of images to generate.

    Returns:
        PipelineTask instance.
    """
    task_id = str(uuid.uuid4())[:8]
    task = PipelineTask(
        task_id=task_id,
        character=character,
        reference_username=reference_username,
        num_images=num_images,
    )
    _tasks[task_id] = task.to_dict()
    return task


def get_task(task_id: str) -> Optional[dict]:
    """Get task status by ID."""
    return _tasks.get(task_id)


def list_tasks() -> list[dict]:
    """List all tasks."""
    return sorted(_tasks.values(), key=lambda t: t.get("created_at", ""), reverse=True)


def _update_task(task_id: str, **kwargs: object) -> None:
    """Update task fields."""
    if task_id in _tasks:
        _tasks[task_id].update(kwargs)


async def run_pipeline(
    task: PipelineTask,
    skip_scraping: bool = False,
    skip_approval: bool = False,
    custom_prompts: Optional[list[str]] = None,
) -> dict:
    """Run the full content generation pipeline.

    Steps:
      1. Check rate limiter
      2. Load brand guide
      3. Research: scrape + analyze + describe reference photos
      4. Creative: generate prompts + images + QC
      5. Publish: assemble post package
      6. Telegram: send for approval
      7. Post (if approved)

    Args:
        task: PipelineTask to execute.
        skip_scraping: If True, skip Instagram scraping (use custom prompts).
        skip_approval: If True, skip Telegram approval step.
        custom_prompts: Optional list of pre-written prompts (skips Research + prompt gen).

    Returns:
        dict with pipeline results.
    """
    task_id = task.task_id

    try:
        # Step 0: Rate limit check
        _update_task(task_id, status="checking_rate_limit", current_step="Rate limit check")
        guide = bg.load_brand_guide(task.character)
        posting_rules = bg.get_posting_rules(guide)

        rate_check = rate_limiter.can_post(
            max_posts_per_day=posting_rules.get("max_posts_per_day", 2),
            cooldown_hours=posting_rules.get("cooldown_hours", 6),
        )

        if not rate_check["allowed"]:
            _update_task(
                task_id,
                status="rate_limited",
                error=rate_check["reason"],
                result=rate_check,
            )
            return {"status": "rate_limited", **rate_check}

        # Step 1: Research
        content_brief: Optional[dict] = None

        if custom_prompts:
            # Skip research, use provided prompts directly
            _update_task(task_id, status="using_custom_prompts", current_step="Using custom prompts")
            content_brief = {
                "image_plans": [
                    {"full_prompt": p, "theme": f"custom_{i}", "outfit_summary": "custom"}
                    for i, p in enumerate(custom_prompts)
                ],
                "caption": "",
                "hashtags": [],
                "carousel_order": list(range(len(custom_prompts))),
            }

        elif not skip_scraping and task.reference_username:
            # Full research pipeline
            _update_task(task_id, status="researching", current_step="Scraping Instagram profile")
            logger.info(f"[{task_id}] Scraping @{task.reference_username}...")

            try:
                profile_data = await research_agent.scrape_profile(
                    username=task.reference_username,
                    max_posts=12,
                )

                # Describe reference photos
                _update_task(task_id, current_step="Describing reference photos")
                all_image_urls = []
                for post in profile_data.get("posts", []):
                    all_image_urls.extend(post.get("image_urls", []))

                ref_descriptions = await research_agent.describe_reference_photos(
                    image_urls=all_image_urls[:6],
                    brand_guide=guide,
                )

                # Analyze profile
                _update_task(task_id, current_step="Analyzing profile")
                profile_analysis = await research_agent.analyze_profile(
                    profile_data=profile_data,
                    brand_guide=guide,
                )

                # Create content brief
                _update_task(task_id, current_step="Creating content brief")
                content_brief = await research_agent.create_content_brief(
                    reference_descriptions=ref_descriptions,
                    profile_analysis=profile_analysis,
                    brand_guide=guide,
                    num_images=task.num_images,
                )

            except Exception as e:
                logger.warning(f"[{task_id}] Research failed: {e}. Falling back to brand guide only.")
                _update_task(task_id, current_step="Research failed, using brand guide defaults")
                content_brief = None

        if content_brief is None:
            # Generate content brief from brand guide alone
            _update_task(task_id, status="generating_brief", current_step="Generating content from brand guide")
            content_brief = await _generate_brief_from_guide(guide, task.num_images)

        # Step 2: Creative — generate images
        _update_task(task_id, status="generating_images", current_step="Generating images via z-image")
        logger.info(f"[{task_id}] Generating {len(content_brief.get('image_plans', []))} images...")

        carousel_data = await creative_agent.generate_carousel(
            content_brief=content_brief,
            brand_guide=guide,
            max_retries=1,
        )

        # Step 3: Publish — assemble post
        _update_task(task_id, status="assembling_post", current_step="Assembling post package")
        logger.info(f"[{task_id}] Assembling post...")

        post_package = await publish_agent.assemble_post(
            carousel_data=carousel_data,
            brand_guide=guide,
        )

        # Step 4: Telegram approval
        if not skip_approval:
            _update_task(task_id, status="awaiting_approval", current_step="Sent to Telegram for approval")
            logger.info(f"[{task_id}] Sending to Telegram for approval...")

            try:
                msg_id = await telegram_bot.send_post_for_approval(post_package)

                # Wait for user decision
                decision = await telegram_bot.wait_for_approval(timeout_minutes=60)

                if decision["decision"] == "approve":
                    _update_task(task_id, current_step="Approved! Recording post.")
                    rate_limiter.record_post()
                    post_package["status"] = "approved"
                elif decision["decision"] == "reject":
                    post_package["status"] = "rejected"
                    _update_task(task_id, status="rejected", current_step="Post rejected by user")
                elif decision["decision"] == "edit_caption":
                    new_caption = decision.get("new_caption", post_package.get("caption", ""))
                    post_package["caption"] = new_caption
                    post_package["status"] = "approved_edited"
                    rate_limiter.record_post()
                elif decision["decision"] == "regenerate":
                    post_package["status"] = "regenerate_requested"
                    _update_task(task_id, status="regenerate_requested", current_step="User requested regeneration")
                else:
                    post_package["status"] = "timeout"

            except Exception as e:
                logger.warning(f"[{task_id}] Telegram approval failed: {e}")
                post_package["status"] = "approval_skipped"
                _update_task(task_id, current_step=f"Telegram approval error: {e}")
        else:
            post_package["status"] = "auto_approved"
            rate_limiter.record_post()

        # Done
        _update_task(
            task_id,
            status="completed",
            current_step="Pipeline complete",
            result=_sanitize_result(post_package),
        )

        return post_package

    except Exception as e:
        logger.error(f"[{task_id}] Pipeline failed: {e}", exc_info=True)
        _update_task(task_id, status="failed", error=str(e), current_step="Pipeline failed")
        return {"status": "failed", "error": str(e)}


async def _generate_brief_from_guide(guide: dict, num_images: int) -> dict:
    """Generate a content brief using only the brand guide (no reference profile)."""
    from app.agents import llm_client

    char = guide.get("character", {})
    style = guide.get("style", {})
    tips = guide.get("prompt_template", {}).get("tips", [])

    messages = [
        {
            "role": "system",
            "content": (
                "You are an Instagram content creator for AI characters. "
                "Generate a content plan based on the character profile.\n\n"
                f"Character: {char.get('name')}, {char.get('age')} years old\n"
                f"Trigger word: {char.get('trigger_word')}\n"
                f"Appearance: {json.dumps(char.get('appearance', {}))}\n"
                f"Style: {json.dumps(style)}\n"
                f"Prompt tips: {json.dumps(tips)}\n\n"
                "Each prompt MUST start with 'A {trigger_word},' and be 500+ characters."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create {num_images} diverse image prompts for an Instagram carousel.\n"
                "Include variety in: outfits, backgrounds, poses, lighting.\n\n"
                "Output JSON with:\n"
                "- image_plans: array of objects with full_prompt (500+ chars), theme, outfit_summary\n"
                "- caption: short Instagram caption with emoji\n"
                "- hashtags: array of 15-20 hashtags\n"
                "- carousel_order: array of indices"
            ),
        },
    ]

    return await llm_client.chat_completion_json(
        messages=messages,
        temperature=0.8,
        max_tokens=6000,
    )


def _sanitize_result(post_package: dict) -> dict:
    """Remove large base64 data from result for storage."""
    sanitized = dict(post_package)
    if "images" in sanitized:
        sanitized["images"] = [
            {k: v for k, v in img.items() if k != "image_base64"}
            for img in sanitized["images"]
        ]
    return sanitized
