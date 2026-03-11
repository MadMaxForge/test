"""Job manager for carousel image generation via Nano Banana 2 (EvoLink).

Follows the same pattern as job_manager.py: in-memory store, background tasks.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.evolink_schemas import EvoLinkTaskStatus
from app.services import evolink_api


# In-memory task store
_carousel_tasks: dict[str, dict] = {}


def create_task(
    prompt: str,
    size: str = "4:5",
    quality: str = "1K",
    reference_image_urls: Optional[list[str]] = None,
) -> str:
    """Create a new carousel image generation task entry."""
    task_id = str(uuid.uuid4())
    _carousel_tasks[task_id] = {
        "task_id": task_id,
        "status": EvoLinkTaskStatus.PENDING,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "image_urls": None,
        "error": None,
        "evolink_task_id": None,
    }
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    return _carousel_tasks.get(task_id)


def list_tasks() -> list[dict]:
    return sorted(
        _carousel_tasks.values(),
        key=lambda t: t["created_at"],
        reverse=True,
    )


def _update_task(task_id: str, **kwargs: object) -> None:
    if task_id in _carousel_tasks:
        _carousel_tasks[task_id].update(kwargs)


async def generate_carousel_image(
    task_id: str,
    prompt: str,
    size: str = "4:5",
    quality: str = "1K",
    reference_image_urls: Optional[list[str]] = None,
) -> None:
    """Submit image generation to EvoLink Nano Banana 2 API.

    Runs as a background task.
    """
    try:
        _update_task(task_id, status=EvoLinkTaskStatus.PROCESSING)

        result = await evolink_api.create_image_task(
            prompt=prompt,
            size=size,
            quality=quality,
            image_urls=reference_image_urls,
        )

        evolink_task_id = result.get("id", "")
        _update_task(task_id, evolink_task_id=evolink_task_id)

        # If the API returned completed immediately (unlikely but possible)
        status = result.get("status", "pending")
        if status == "completed":
            images = _extract_image_urls(result)
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.COMPLETED,
                image_urls=images,
            )
        elif status == "failed":
            error_msg = result.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", "Image generation failed")
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.FAILED,
                error=str(error_msg),
            )

    except Exception as e:
        _update_task(
            task_id,
            status=EvoLinkTaskStatus.FAILED,
            error=str(e),
        )


async def poll_task_status(task_id: str) -> Optional[list[str]]:
    """Poll EvoLink for image task status. Returns image URLs if completed."""
    task = get_task(task_id)
    if not task or not task.get("evolink_task_id"):
        return None

    try:
        result = await evolink_api.get_image_task(task["evolink_task_id"])
        status = result.get("status", "")

        if status == "completed":
            images = _extract_image_urls(result)
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.COMPLETED,
                image_urls=images,
            )
            return images

        elif status == "failed":
            error_msg = result.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", "Image generation failed")
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.FAILED,
                error=str(error_msg),
            )

        # Still processing
        return None

    except Exception as e:
        _update_task(
            task_id,
            status=EvoLinkTaskStatus.FAILED,
            error=f"Polling error: {e}",
        )
        return None


def _extract_image_urls(result: dict) -> list[str]:
    """Extract image URLs from EvoLink Nano Banana 2 response.

    Response format when completed:
    {
        "data": [{"url": "https://...", "revised_prompt": "..."}]
    }
    """
    urls: list[str] = []
    data = result.get("data", [])
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                url = item.get("url", "")
                if url:
                    urls.append(url)
    return urls
