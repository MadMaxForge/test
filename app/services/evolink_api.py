"""EvoLink.ai API client for Kling Motion Control and Nano Banana 2.

EvoLink provides a unified API gateway (similar to OpenRouter) for accessing
multiple AI generation models. Base URL: https://api.evolink.ai

Both endpoints are async (submit task → poll for result).
"""

import httpx
import os
from typing import Optional


EVOLINK_BASE_URL = "https://api.evolink.ai"


def _get_api_key() -> str:
    key = os.getenv("EVOLINK_API_KEY", "")
    if not key:
        raise ValueError("EVOLINK_API_KEY not set")
    return key


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


# ── Nano Banana 2 (Image Generation) ──────────────────────────────


async def create_image_task(
    prompt: str,
    size: str = "auto",
    quality: str = "1K",
    image_urls: Optional[list[str]] = None,
    callback_url: Optional[str] = None,
) -> dict:
    """Create a Nano Banana 2 image generation task.

    POST /v1/images/generations
    Model: gemini-3.1-flash-image-preview

    Args:
        prompt: Text description of the image to generate (max 2000 chars).
        size: Aspect ratio — auto, 1:1, 4:5, 9:16, 16:9, 3:4, 4:3, etc.
        quality: Resolution — 0.5K, 1K, 2K, 4K.
        image_urls: Optional reference image URLs for image-to-image / editing.
        callback_url: Optional HTTPS webhook for task completion.

    Returns:
        dict with 'id' (task ID), 'status', 'model', etc.
    """
    payload: dict = {
        "model": "gemini-3.1-flash-image-preview",
        "prompt": prompt,
        "size": size,
        "quality": quality,
    }
    if image_urls:
        payload["image_urls"] = image_urls
    if callback_url:
        payload["callback_url"] = callback_url

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{EVOLINK_BASE_URL}/v1/images/generations",
            headers=_auth_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def get_image_task(task_id: str) -> dict:
    """Poll Nano Banana 2 image generation task status.

    GET /v1/images/generations/{task_id}

    Returns:
        dict with 'status', 'data' (list of generated images when completed).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{EVOLINK_BASE_URL}/v1/images/generations/{task_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ── Kling V3 Motion Control (Video Generation) ────────────────────


async def create_motion_control_task(
    image_url: str,
    video_url: str,
    character_orientation: str = "video",
    prompt: Optional[str] = None,
    quality: str = "720p",
    keep_sound: bool = False,
    callback_url: Optional[str] = None,
) -> dict:
    """Create a Kling V3 Motion Control video generation task.

    POST /v1/videos/generations
    Model: kling-v3-motion-control

    Args:
        image_url: Character reference image URL (JPG/PNG, ≤10MB).
        video_url: Motion reference video URL (MP4/MOV, 3–30s, ≤100MB).
        character_orientation: 'video' (match video orientation, max 30s)
                               or 'image' (match image orientation, max 10s).
        prompt: Optional text guidance (max 2500 chars).
        quality: '720p' (standard) or '1080p' (pro).
        keep_sound: Keep original audio from reference video.
        callback_url: Optional HTTPS webhook for task completion.

    Returns:
        dict with 'id' (task ID), 'status', 'model', etc.
    """
    model_params: dict = {
        "character_orientation": character_orientation,
        "keep_sound": keep_sound,
    }

    payload: dict = {
        "model": "kling-v3-motion-control",
        "image_urls": [image_url],
        "video_urls": [video_url],
        "quality": quality,
        "model_params": model_params,
    }
    if prompt:
        payload["prompt"] = prompt
    if callback_url:
        payload["callback_url"] = callback_url

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{EVOLINK_BASE_URL}/v1/videos/generations",
            headers=_auth_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def get_video_task(task_id: str) -> dict:
    """Poll Kling Motion Control video generation task status.

    GET /v1/videos/generations/{task_id}

    Returns:
        dict with 'status', 'data' (video results when completed).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{EVOLINK_BASE_URL}/v1/videos/generations/{task_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()
