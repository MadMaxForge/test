"""Job manager for reel generation via Kling V3 Motion Control (EvoLink).

Follows the same pattern as job_manager.py: in-memory store, background tasks.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.evolink_schemas import EvoLinkTaskStatus
from app.services import evolink_api


# In-memory task store
_reel_tasks: dict[str, dict] = {}


def create_task(
    character_image_url: str,
    reference_video_url: str,
    prompt: Optional[str] = None,
    character_orientation: str = "video",
    quality: str = "720p",
    keep_sound: bool = False,
) -> str:
    """Create a new reel generation task entry."""
    task_id = str(uuid.uuid4())
    _reel_tasks[task_id] = {
        "task_id": task_id,
        "status": EvoLinkTaskStatus.PENDING,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "character_image_url": character_image_url,
        "reference_video_url": reference_video_url,
        "prompt": prompt,
        "quality": quality,
        "video_url": None,
        "error": None,
        "evolink_task_id": None,
    }
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    return _reel_tasks.get(task_id)


def list_tasks() -> list[dict]:
    return sorted(
        _reel_tasks.values(),
        key=lambda t: t["created_at"],
        reverse=True,
    )


def _update_task(task_id: str, **kwargs: object) -> None:
    if task_id in _reel_tasks:
        _reel_tasks[task_id].update(kwargs)


async def generate_reel(
    task_id: str,
    character_image_url: str,
    reference_video_url: str,
    prompt: Optional[str] = None,
    character_orientation: str = "video",
    quality: str = "720p",
    keep_sound: bool = False,
) -> None:
    """Submit video generation to EvoLink Kling V3 Motion Control API.

    Runs as a background task.
    """
    try:
        _update_task(task_id, status=EvoLinkTaskStatus.PROCESSING)

        result = await evolink_api.create_motion_control_task(
            image_url=character_image_url,
            video_url=reference_video_url,
            character_orientation=character_orientation,
            prompt=prompt,
            quality=quality,
            keep_sound=keep_sound,
        )

        evolink_task_id = result.get("id", "")
        _update_task(task_id, evolink_task_id=evolink_task_id)

        # Check if returned completed immediately
        status = result.get("status", "pending")
        if status == "completed":
            video_url = _extract_video_url(result)
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.COMPLETED,
                video_url=video_url,
            )
        elif status == "failed":
            error_msg = result.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", "Video generation failed")
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


async def poll_task_status(task_id: str) -> Optional[str]:
    """Poll EvoLink for video task status. Returns video URL if completed."""
    task = get_task(task_id)
    if not task or not task.get("evolink_task_id"):
        return None

    try:
        result = await evolink_api.get_video_task(task["evolink_task_id"])
        status = result.get("status", "")

        if status == "completed":
            video_url = _extract_video_url(result)
            _update_task(
                task_id,
                status=EvoLinkTaskStatus.COMPLETED,
                video_url=video_url,
            )
            return video_url

        elif status == "failed":
            error_msg = result.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", "Video generation failed")
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


def _extract_video_url(result: dict) -> Optional[str]:
    """Extract video URL from EvoLink Kling Motion Control response.

    Response format when completed:
    {
        "data": [{"url": "https://...", "duration": 5.0}]
    }
    """
    data = result.get("data", [])
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                url = item.get("url", "")
                if url:
                    return url
    return None
