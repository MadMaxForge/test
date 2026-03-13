"""
Nano Banana 2 Generator — generates non-character images via Evolink API.

Used for world content: interiors, objects, food, products, textures,
backgrounds — everything that does NOT contain the main character.

This is a pure Python module — no LLM calls.
"""

import json
import time
import requests
from pathlib import Path

from content_module.core.config import (
    EVOLINK_API_KEY,
    EVOLINK_BASE_URL,
    NANO_BANANA_MODEL,
    ASPECT_RATIOS,
    JOBS_DIR,
)

# Map content_type to Evolink aspect ratio string
SIZE_MAP = {
    "post": "4:5",
    "story": "9:16",
    "reel": "9:16",
    "square": "1:1",
    "landscape": "16:9",
}


def generate_image(
    prompt: str,
    content_type: str = "post",
    model: str = NANO_BANANA_MODEL,
    poll_interval: int = 10,
    max_wait: int = 600,
) -> bytes:
    """
    Submit an image generation task to Evolink and poll until complete.

    Args:
        prompt: Text prompt for the image (no w1man trigger needed)
        content_type: 'post' (4:5), 'story' (9:16), 'reel' (9:16)
        model: Evolink model name
        poll_interval: Seconds between status checks
        max_wait: Maximum wait time in seconds

    Returns:
        Image bytes (PNG/JPG)

    Raises:
        Exception on failure or timeout
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    size = SIZE_MAP.get(content_type, ASPECT_RATIOS.get(content_type, "4:5"))

    print(f"[NanoBanana] Submitting to {model} (size={size})...")

    # 1. Submit task
    resp = requests.post(
        f"{EVOLINK_BASE_URL}/images/generations",
        headers=headers,
        json={
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"Evolink submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    status = task_data.get("status", "unknown")

    if not task_id:
        raise Exception(f"No task ID in response: {task_data}")

    print(f"[NanoBanana] Task: {task_id} (status={status})")
    estimated = task_data.get("task_info", {}).get("estimated_time", "?")
    print(f"[NanoBanana] Estimated time: {estimated}s")

    # Check immediate completion
    if status in ("completed", "success"):
        return _extract_image(task_data)

    # 2. Poll for result
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for task {task_id}")

        time.sleep(poll_interval)

        status_resp = requests.get(
            f"{EVOLINK_BASE_URL}/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )

        if status_resp.status_code != 200:
            print(f"[NanoBanana] Status check failed ({status_resp.status_code}), retrying...")
            continue

        data = status_resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)

        if status in ("completed", "success"):
            print(f"[NanoBanana] Completed in {elapsed:.0f}s")
            return _extract_image(data)

        if status in ("failed", "error"):
            error = data.get("error", data)
            raise Exception(f"Task failed: {error}")

        if status in ("pending", "processing", "running"):
            print(f"[NanoBanana] {status} (progress={progress}, {elapsed:.0f}s elapsed)...")
        else:
            print(f"[NanoBanana] Unknown status: {status} ({elapsed:.0f}s elapsed)")


def _extract_image(data: dict) -> bytes:
    """Extract image bytes from completed task response."""
    images = data.get("results", [])
    if not images:
        images = data.get("data", [])
    if not images:
        output = data.get("output", {})
        images = output.get("images", output.get("data", []))
    if not images:
        raise Exception(f"No images in response: {json.dumps(data)[:500]}")

    url = None
    if isinstance(images, list) and len(images) > 0:
        item = images[0]
        if isinstance(item, dict):
            url = item.get("url") or item.get("image_url")
        elif isinstance(item, str):
            url = item

    if not url:
        raise Exception(f"Could not extract image URL: {images}")

    print(f"[NanoBanana] Downloading image from {url[:80]}...")
    img_resp = requests.get(url, timeout=120)
    if img_resp.status_code != 200:
        raise Exception(f"Failed to download image ({img_resp.status_code})")

    return img_resp.content


def save_image(img_bytes: bytes, job_id: str, asset_index: int) -> str:
    """Save generated image to the job's generated directory."""
    gen_dir = JOBS_DIR / job_id / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    filename = f"asset_{asset_index:02d}_nanobanana.png"
    filepath = gen_dir / filename
    filepath.write_bytes(img_bytes)
    print(f"[NanoBanana] Saved: {filepath} ({len(img_bytes) / 1024:.0f} KB)")
    return str(filepath)
