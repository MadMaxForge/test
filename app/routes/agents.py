"""API routes for the Instagram Agent System.

Provides endpoints to:
  - Manage brand guides
  - Run the full pipeline (Research -> Creative -> Publish -> Approve)
  - Generate individual images
  - Check task status
  - Manage rate limiter
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.agents import brand_guide as bg
from app.agents import orchestrator, rate_limiter
from app.agents import creative_agent, publish_agent, telegram_bot

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
