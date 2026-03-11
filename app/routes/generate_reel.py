"""Routes for reel generation via Kling V3 Motion Control (EvoLink)."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.evolink_schemas import (
    ReelRequest,
    ReelResponse,
    ReelListResponse,
    EvoLinkTaskStatus,
)
from app.services import reel_manager

router = APIRouter(prefix="/api", tags=["reels"])


@router.post("/generate-reel", response_model=ReelResponse)
async def generate_reel(
    request: ReelRequest,
    background_tasks: BackgroundTasks,
):
    """Generate a reel via Kling V3 Motion Control.

    The agent provides:
    1. character_image_url — image of the character (generated via ComfyUI + LoRA)
    2. reference_video_url — a trending dance/reel video to copy motion from
    3. prompt (optional) — scene/environment description

    The Kling model extracts motion from the reference video and applies it
    to the character image, producing a new video where the character performs
    the same movements.

    Workflow for the agent:
    1. Parse trending reels with dancing girls
    2. Generate character image via ComfyUI + LoRA endpoint
    3. POST /api/generate-reel with both URLs
    4. Poll GET /api/reel-tasks/{task_id} until completed
    5. Post the resulting video to Instagram
    """
    task_id = reel_manager.create_task(
        character_image_url=request.character_image_url,
        reference_video_url=request.reference_video_url,
        prompt=request.prompt,
        character_orientation=request.character_orientation,
        quality=request.quality,
        keep_sound=request.keep_sound,
    )

    background_tasks.add_task(
        reel_manager.generate_reel,
        task_id=task_id,
        character_image_url=request.character_image_url,
        reference_video_url=request.reference_video_url,
        prompt=request.prompt,
        character_orientation=request.character_orientation,
        quality=request.quality,
        keep_sound=request.keep_sound,
    )

    task = reel_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return ReelResponse(**task)


@router.get("/reel-tasks/{task_id}", response_model=ReelResponse)
async def get_reel_task(task_id: str):
    """Get the status of a reel generation task.

    Returns video_url when completed.
    """
    task = reel_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Poll EvoLink if still processing
    if task["status"] in (EvoLinkTaskStatus.PENDING, EvoLinkTaskStatus.PROCESSING):
        if task.get("evolink_task_id"):
            await reel_manager.poll_task_status(task_id)
            task = reel_manager.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

    return ReelResponse(**task)


@router.get("/reel-tasks", response_model=ReelListResponse)
async def list_reel_tasks():
    """List all reel generation tasks."""
    tasks = reel_manager.list_tasks()
    return ReelListResponse(
        tasks=[ReelResponse(**t) for t in tasks],
        total=len(tasks),
    )
