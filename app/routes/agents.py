"""API routes for the Instagram Agent System.

Provides endpoints to:
  - Manage brand guides
  - Run the full pipeline (Research -> Creative -> Publish -> Approve)
  - Generate individual images
  - Check task status
  - Manage rate limiter
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.agents import brand_guide as bg
from app.agents import orchestrator, rate_limiter
from app.agents import creative_agent, publish_agent, telegram_bot
from app.agents import carousel_composer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ---------- Request / Response Models ----------

class PipelineRequest(BaseModel):
    character: str = Field(..., description="Brand guide character name (e.g. 'lanna_danger')")
    reference_username: Optional[str] = Field(None, description="Instagram username for reference")
    num_images: int = Field(4, ge=1, le=10, description="Number of images to generate")
    skip_scraping: bool = Field(False, description="Skip Instagram scraping")
    skip_approval: bool = Field(False, description="Skip Telegram approval")
    custom_prompts: Optional[list[str]] = Field(None, description="Pre-written prompts (skips research)")


class GenerateImageRequest(BaseModel):
    character: str = Field(..., description="Brand guide character name")
    prompt: str = Field(..., min_length=50, description="Full z-image prompt (500+ chars recommended)")
    width: Optional[int] = Field(None, description="Image width (default from brand guide)")
    height: Optional[int] = Field(None, description="Image height (default from brand guide)")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")


class GeneratePromptRequest(BaseModel):
    character: str = Field(..., description="Brand guide character name")
    scene_idea: str = Field(..., min_length=10, description="Scene concept (e.g. 'cosplay in neon bedroom')")


class TaskResponse(BaseModel):
    task_id: str
    status: str
    current_step: str = ""
    character: str = ""
    created_at: str = ""
    error: Optional[str] = None
    result: Optional[dict] = None


# ---------- Brand Guide Endpoints ----------

@router.get("/brand-guides")
async def list_brand_guides():
    """List all available brand guides."""
    guides = bg.list_brand_guides()
    return {"guides": guides}


@router.get("/brand-guides/{character}")
async def get_brand_guide(character: str):
    """Get a specific brand guide."""
    try:
        guide = bg.load_brand_guide(character)
        return guide
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Brand guide '{character}' not found")


# ---------- Pipeline Endpoints ----------

@router.post("/pipeline/run", response_model=TaskResponse)
async def run_pipeline(request: PipelineRequest, background_tasks: BackgroundTasks):
    """Start the full content generation pipeline.

    The pipeline runs in the background:
    Research -> Creative -> Publish -> Telegram Approval
    """
    # Validate brand guide exists
    try:
        bg.load_brand_guide(request.character)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Brand guide '{request.character}' not found")

    # Create task
    task = orchestrator.create_task(
        character=request.character,
        reference_username=request.reference_username,
        num_images=request.num_images,
    )

    # Run pipeline in background
    background_tasks.add_task(
        orchestrator.run_pipeline,
        task=task,
        skip_scraping=request.skip_scraping,
        skip_approval=request.skip_approval,
        custom_prompts=request.custom_prompts,
    )

    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        current_step=task.current_step,
        character=task.character,
        created_at=task.created_at,
    )


@router.get("/pipeline/tasks", response_model=list[TaskResponse])
async def list_tasks():
    """List all pipeline tasks."""
    tasks = orchestrator.list_tasks()
    return [TaskResponse(**t) for t in tasks]


@router.get("/pipeline/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get status of a specific pipeline task."""
    task = orchestrator.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(**task)


# ---------- Image Generation Endpoints ----------

@router.post("/generate/prompt")
async def generate_prompt(request: GeneratePromptRequest):
    """Generate a detailed z-image prompt from a scene idea."""
    try:
        guide = bg.load_brand_guide(request.character)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Brand guide '{request.character}' not found")

    prompt = await creative_agent.generate_prompt(
        scene_idea=request.scene_idea,
        brand_guide=guide,
    )

    return {
        "prompt": prompt,
        "length": len(prompt),
        "character": request.character,
    }


@router.post("/generate/image")
async def generate_image(request: GenerateImageRequest, background_tasks: BackgroundTasks):
    """Submit a single image generation job to RunPod z-image.

    Returns the RunPod job ID. Use /generate/image/{job_id}/status to poll.
    """
    try:
        guide = bg.load_brand_guide(request.character)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Brand guide '{request.character}' not found")

    result = await creative_agent.generate_image(
        prompt=request.prompt,
        brand_guide=guide,
        seed=request.seed,
        width=request.width,
        height=request.height,
    )

    return result


@router.get("/generate/image/{runpod_job_id}/status")
async def check_image_status(runpod_job_id: str):
    """Check status of a RunPod z-image job."""
    from app.services.runpod_api import check_job_status
    result = await check_job_status(runpod_job_id)
    return result


# ---------- Rate Limiter Endpoints ----------

@router.get("/rate-limiter/status")
async def rate_limiter_status(
    max_posts: int = Query(2, description="Max posts per day"),
):
    """Get current rate limiter status."""
    return rate_limiter.get_status(max_posts_per_day=max_posts)


@router.get("/rate-limiter/can-post")
async def can_post(
    max_posts: int = Query(2),
    cooldown_hours: float = Query(6.0),
):
    """Check if posting is currently allowed."""
    return rate_limiter.can_post(
        max_posts_per_day=max_posts,
        cooldown_hours=cooldown_hours,
    )


@router.post("/rate-limiter/reset")
async def reset_rate_limiter():
    """Reset rate limiter state (for testing)."""
    rate_limiter.reset_state()
    return {"status": "reset"}


# ---------- Telegram Bot Endpoints ----------

@router.get("/telegram/discover-chat")
async def discover_telegram_chat():
    """Discover Telegram chat ID from recent messages.

    User must send /start to the bot first.
    """
    chat_id = await telegram_bot.discover_chat_id()
    if chat_id:
        return {"chat_id": chat_id, "status": "found"}
    return {"chat_id": None, "status": "not_found", "hint": "Send /start to @Devin_open_code_bot first"}


@router.post("/telegram/test-message")
async def send_test_message(
    text: str = Query("Hello from Instagram Agent System!"),
    chat_id: Optional[str] = Query(None),
):
    """Send a test message via Telegram bot."""
    try:
        result = await telegram_bot.send_message(text=text, chat_id=chat_id)
        return {"status": "sent", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Demo Pipeline Endpoint ----------

class DemoPipelineRequest(BaseModel):
    character: str = Field("lanna_danger", description="Brand guide character name")
    scene_idea: str = Field(
        "casual bedroom selfie with neon lights",
        description="Scene concept for image generation",
    )
    num_images: int = Field(1, ge=1, le=6, description="Number of images (1=single, 2+=carousel)")
    send_to_telegram: bool = Field(True, description="Send preview to Telegram")


@router.post("/pipeline/demo")
async def demo_pipeline(request: DemoPipelineRequest, background_tasks: BackgroundTasks):
    """Run a demo pipeline: generate 1 image + caption → send preview to Telegram.

    Skips Instagram scraping. Generates a single image with full prompt,
    assembles a post package, and sends it to Telegram for preview.
    """
    try:
        guide = bg.load_brand_guide(request.character)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Brand guide '{request.character}' not found")

    # Create task
    task = orchestrator.create_task(
        character=request.character,
        num_images=request.num_images,
    )

    # Run in background
    background_tasks.add_task(
        _run_demo_pipeline,
        task=task,
        scene_idea=request.scene_idea,
        guide=guide,
        send_to_telegram=request.send_to_telegram,
        num_images=request.num_images,
    )

    return {
        "task_id": task.task_id,
        "status": "started",
        "message": "Demo pipeline started. Poll /pipeline/tasks/{task_id} for status.",
    }


async def _run_demo_pipeline(
    task: orchestrator.PipelineTask,
    scene_idea: str,
    guide: dict,
    send_to_telegram: bool = True,
    num_images: int = 1,
    max_qc_retries: int = 2,
) -> None:
    """Execute demo pipeline in background.

    Flow: Generate prompt(s) → Generate image(s) → QC check each → Retry if QC fails
          → Generate caption/hashtags → Send to Telegram.

    Supports single image or carousel (num_images > 1).
    """
    task_id = task.task_id

    try:
        # Step 1: Generate prompts — one per image, with variety
        logger.info(f"[demo:{task_id}] Step 1: Generating {num_images} prompt(s) for scene='{scene_idea}'")
        orchestrator._update_task(
            task_id, status="generating_prompt",
            current_step=f"Generating {num_images} prompt(s) via LLM",
        )

        prompts: list[str] = []
        if num_images == 1:
            prompt = await creative_agent.generate_prompt(
                scene_idea=scene_idea,
                brand_guide=guide,
            )
            prompts.append(prompt)
        else:
            # Generate varied prompts for carousel
            variations = [
                scene_idea,
                f"{scene_idea}, different angle and outfit",
                f"{scene_idea}, close-up portrait shot",
                f"{scene_idea}, full body shot with different pose",
                f"{scene_idea}, candid style with natural lighting",
                f"{scene_idea}, dramatic lighting and moody atmosphere",
            ]
            for i in range(num_images):
                variation = variations[i] if i < len(variations) else f"{scene_idea}, variation {i + 1}"
                p = await creative_agent.generate_prompt(
                    scene_idea=variation,
                    brand_guide=guide,
                )
                prompts.append(p)
                logger.info(f"[demo:{task_id}] Prompt {i + 1}/{num_images} generated ({len(p)} chars)")

        # Step 2-4: Generate images + QC with retries (for each prompt)
        all_images: list[dict] = []
        all_qc_results: list[dict] = []

        for img_idx, prompt in enumerate(prompts):
            image_data = None
            qc_result = None
            best_image = None
            best_qc_score = 0
            best_qc: dict = {}

            for attempt in range(1, max_qc_retries + 1):
                step_label = f"image {img_idx + 1}/{num_images}" if num_images > 1 else "image"
                logger.info(f"[demo:{task_id}] Generating {step_label} (attempt {attempt}/{max_qc_retries})")
                orchestrator._update_task(
                    task_id, status="generating_image",
                    current_step=f"Generating {step_label} (attempt {attempt}/{max_qc_retries})",
                )
                job_info = await creative_agent.generate_image(
                    prompt=prompt,
                    brand_guide=guide,
                )
                logger.info(f"[demo:{task_id}] RunPod job submitted: {job_info.get('runpod_job_id')}")

                orchestrator._update_task(task_id, current_step=f"Waiting for RunPod job {job_info['runpod_job_id']}")
                result = await creative_agent.wait_for_image(
                    runpod_job_id=job_info["runpod_job_id"],
                    timeout_seconds=300,
                )

                if result["status"] != "completed":
                    logger.warning(f"[demo:{task_id}] Image generation failed: {result.get('error')}")
                    continue

                output = result.get("output", {})
                candidate = creative_agent._extract_image_from_output(output)

                if not candidate or not candidate.get("base64"):
                    logger.warning(f"[demo:{task_id}] Could not extract image from output")
                    continue

                # QC check
                logger.info(f"[demo:{task_id}] Running QC for {step_label} (attempt {attempt})")
                orchestrator._update_task(
                    task_id, status="quality_check",
                    current_step=f"QC check {step_label} (attempt {attempt}/{max_qc_retries})",
                )
                qc_result = await creative_agent.quality_check(
                    image_base64=candidate["base64"],
                    original_prompt=prompt,
                    brand_guide=guide,
                )

                score = qc_result.get("score", 0)
                passed = qc_result.get("passed", False)
                issues = qc_result.get("issues", [])
                logger.info(
                    f"[demo:{task_id}] QC result: score={score}, passed={passed}, issues={issues}"
                )

                if score > best_qc_score:
                    best_qc_score = score
                    best_image = candidate
                    best_qc = qc_result

                if passed:
                    logger.info(f"[demo:{task_id}] QC passed with score {score}!")
                    image_data = candidate
                    break
                else:
                    logger.info(
                        f"[demo:{task_id}] QC rejected (score {score}): {issues}. "
                        f"{'Retrying...' if attempt < max_qc_retries else 'No more retries.'}"
                    )

            # Use best image if none passed QC
            if image_data is None and best_image is not None:
                image_data = best_image
                qc_result = best_qc
                logger.warning(f"[demo:{task_id}] No image passed QC for slot {img_idx}. Using best (score={best_qc_score})")

            if image_data is not None and qc_result is not None:
                all_images.append({
                    "index": img_idx,
                    "theme": scene_idea,
                    "prompt": prompt,
                    "qc_score": qc_result.get("score", 0),
                    "qc_passed": qc_result.get("passed", False),
                    "qc_issues": qc_result.get("issues", []),
                    "image_base64": image_data["base64"],
                })
                all_qc_results.append(qc_result)

        if not all_images:
            orchestrator._update_task(
                task_id, status="failed",
                error="All image generation attempts failed",
                current_step="Image generation failed after all retries",
            )
            return

        # Step 5: Generate caption + hashtags
        logger.info(f"[demo:{task_id}] Step 5: Generating caption + hashtags for {len(all_images)} images")
        orchestrator._update_task(task_id, status="assembling_post", current_step="Generating caption and hashtags")

        carousel_data = {
            "images": all_images,
            "caption": "",
            "hashtags": [],
            "carousel_order": list(range(len(all_images))),
        }

        post_package = await publish_agent.assemble_post(
            carousel_data=carousel_data,
            brand_guide=guide,
        )

        # Attach aggregate QC info to post package
        qc_scores = [qc.get("score", 0) for qc in all_qc_results]
        any_passed = any(qc.get("passed", False) for qc in all_qc_results)
        all_passed = all(qc.get("passed", False) for qc in all_qc_results)
        all_issues: list[str] = []
        for qc in all_qc_results:
            all_issues.extend(qc.get("issues", []))

        post_package["qc_passed"] = all_passed
        post_package["qc_score"] = round(sum(qc_scores) / len(qc_scores), 1) if qc_scores else 0
        post_package["qc_issues"] = all_issues[:10]  # Limit to 10 issues
        best_feedback = max(all_qc_results, key=lambda q: q.get("score", 0)).get("feedback", "") if all_qc_results else ""
        post_package["qc_feedback"] = best_feedback

        # Step 6: Send to Telegram
        if send_to_telegram:
            logger.info(f"[demo:{task_id}] Step 6: Sending preview to Telegram ({len(all_images)} images)")
            orchestrator._update_task(task_id, status="sending_to_telegram", current_step="Sending preview to Telegram")
            try:
                await telegram_bot.send_post_for_approval(post_package)
                orchestrator._update_task(
                    task_id, status="awaiting_approval",
                    current_step="Preview sent to Telegram — waiting for approval",
                    result={
                        "prompts": prompts,
                        "caption": post_package.get("caption", ""),
                        "hashtags": post_package.get("hashtags", []),
                        "image_count": len(all_images),
                        "qc_score": post_package.get("qc_score", 0),
                        "qc_passed": post_package.get("qc_passed", False),
                        "qc_issues": post_package.get("qc_issues", []),
                        "status": "sent_to_telegram",
                    },
                )
            except Exception as e:
                orchestrator._update_task(
                    task_id, status="completed",
                    current_step=f"Telegram send failed: {e}",
                    error=str(e),
                    result={
                        "prompts": prompts,
                        "caption": post_package.get("caption", ""),
                        "status": "telegram_failed",
                    },
                )
        else:
            orchestrator._update_task(
                task_id, status="completed",
                current_step="Demo pipeline complete (no Telegram)",
                result={
                    "prompts": prompts,
                    "caption": post_package.get("caption", ""),
                    "hashtags": post_package.get("hashtags", []),
                    "image_count": len(all_images),
                    "qc_score": post_package.get("qc_score", 0),
                    "qc_passed": post_package.get("qc_passed", False),
                    "status": "completed",
                },
            )

    except Exception as e:
        orchestrator._update_task(
            task_id, status="failed",
            error=str(e),
            current_step=f"Pipeline failed: {e}",
        )
