"""Routes for carousel image generation via Nano Banana 2 (EvoLink)."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.evolink_schemas import (
    CarouselImageRequest,
    CarouselImageResponse,
    CarouselImageListResponse,
    EvoLinkTaskStatus,
)
from app.services import carousel_manager

router = APIRouter(prefix="/api", tags=["carousel"])


@router.post("/generate-carousel", response_model=CarouselImageResponse)
async def generate_carousel_image(
    request: CarouselImageRequest,
    background_tasks: BackgroundTasks,
):
    """Generate a lifestyle/carousel image via Nano Banana 2.

    The agent sends a prompt describing the desired image (e.g. 'A cappuccino
    in a cozy café, warm tones, Instagram aesthetic') and receives a task ID.
    Poll GET /api/carousel-tasks/{task_id} for the result.

    Typical use cases:
    - Atmospheric carousel photos (coffee, makeup, accessories, branded items)
    - Story covers and post backgrounds
    - Any image that does NOT require the character's face/identity
    """
    task_id = carousel_manager.create_task(
        prompt=request.prompt,
        size=request.size,
        quality=request.quality,
        reference_image_urls=request.reference_image_urls,
    )

    background_tasks.add_task(
        carousel_manager.generate_carousel_image,
        task_id=task_id,
        prompt=request.prompt,
        size=request.size,
        quality=request.quality,
        reference_image_urls=request.reference_image_urls,
    )

    task = carousel_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return CarouselImageResponse(**task)


@router.get("/carousel-tasks/{task_id}", response_model=CarouselImageResponse)
async def get_carousel_task(task_id: str):
    """Get the status of a carousel image generation task.

    Returns image_urls when completed.
    """
    task = carousel_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Poll EvoLink if still processing
    if task["status"] in (EvoLinkTaskStatus.PENDING, EvoLinkTaskStatus.PROCESSING):
        if task.get("evolink_task_id"):
            await carousel_manager.poll_task_status(task_id)
            task = carousel_manager.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

    return CarouselImageResponse(**task)


@router.get("/carousel-tasks", response_model=CarouselImageListResponse)
async def list_carousel_tasks():
    """List all carousel image generation tasks."""
    tasks = carousel_manager.list_tasks()
    return CarouselImageListResponse(
        tasks=[CarouselImageResponse(**t) for t in tasks],
        total=len(tasks),
    )
