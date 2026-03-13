"""
Kling Generator — generates video reels via Evolink API (Kling v3).

Supports:
  - motion_control: reference video movement transfer (primary use case)
  - image_to_video: start frame + prompt
  - text_to_video: prompt only (fallback)

For Instagram Reels: generates short videos (5-10s) with motion from reference.

Important: Kling should ONLY be called AFTER the start frame is approved by the user.

This is a pure Python module — no LLM calls.
"""

import base64
import json
import os
import time
import requests
from pathlib import Path

from content_module.core.config import (
    EVOLINK_API_KEY,
    EVOLINK_BASE_URL,
    EVOLINK_FILES_URL,
    KLING_MODELS,
    JOBS_DIR,
)


def _encode_file_base64(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _image_to_data_url(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/png")
    b64 = _encode_file_base64(filepath)
    return f"data:{mime};base64,{b64}"


def _upload_file_to_evolink(filepath: str) -> str:
    """Upload a file to Evolink file service and return the public URL."""
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
    }
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".mp4", ".mov", ".avi", ".webm"):
        # Stream upload for videos
        with open(filepath, "rb") as f:
            files = {"file": (filename, f, "video/mp4")}
            resp = requests.post(
                f"{EVOLINK_FILES_URL}/upload",
                headers=headers,
                files=files,
                timeout=120,
            )
    else:
        # Base64 upload for images
        data_url = _image_to_data_url(filepath)
        resp = requests.post(
            f"{EVOLINK_FILES_URL}/upload",
            headers=headers,
            json={"file": data_url, "filename": filename},
            timeout=60,
        )

    if resp.status_code != 200:
        raise Exception(f"File upload failed ({resp.status_code}): {resp.text[:300]}")

    result = resp.json()
    file_url = result.get("url") or result.get("file_url") or result.get("data", {}).get("url")
    if not file_url:
        raise Exception(f"No URL in upload response: {result}")

    print(f"[Kling] Uploaded: {filename} -> {file_url[:80]}...")
    return file_url


def _poll_task(task_id: str, poll_interval: int = 15, max_wait: int = 600) -> dict:
    """Poll Evolink task until completion."""
    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for task {task_id}")

        time.sleep(poll_interval)

        resp = requests.get(
            f"{EVOLINK_BASE_URL}/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[Kling] Status check failed ({resp.status_code}), retrying...")
            continue

        data = resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)

        if status in ("completed", "success"):
            print(f"[Kling] Completed in {elapsed:.0f}s")
            return data

        if status in ("failed", "error"):
            error = data.get("error", data)
            raise Exception(f"Kling task failed: {error}")

        print(f"[Kling] {status} (progress={progress}, {elapsed:.0f}s elapsed)...")


def _extract_video(data: dict) -> tuple[bytes, str]:
    """Extract video bytes and URL from completed task response."""
    results = data.get("results", [])
    if not results:
        results = data.get("data", [])
    if not results:
        output = data.get("output", {})
        results = output.get("videos", output.get("data", []))

    url = None
    if isinstance(results, list) and len(results) > 0:
        item = results[0]
        if isinstance(item, dict):
            url = item.get("url") or item.get("video_url")
        elif isinstance(item, str):
            url = item

    if not url:
        raise Exception(f"Could not extract video URL: {results}")

    print(f"[Kling] Downloading video from {url[:80]}...")
    resp = requests.get(url, timeout=300)
    if resp.status_code != 200:
        raise Exception(f"Failed to download video ({resp.status_code})")

    return resp.content, url


def motion_control(
    reference_video_path: str,
    start_image_path: str,
    prompt: str = "",
    duration: int = 5,
    character_orientation: str = "image",
) -> tuple[bytes, str]:
    """
    Generate video using motion control: transfer movement from reference video
    onto the character in the start image.

    This is the primary reel generation method.

    Args:
        reference_video_path: Path to motion reference video
        start_image_path: Path to start frame image (Z-Image output)
        prompt: Optional additional prompt
        duration: Video duration in seconds (5 or 10)
        character_orientation: 'image' or 'video'

    Returns:
        Tuple of (video_bytes, video_url)
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"[Kling] Motion control: ref={reference_video_path}, frame={start_image_path}")

    # Upload files to Evolink
    ref_url = _upload_file_to_evolink(reference_video_path)
    img_url = _upload_file_to_evolink(start_image_path)

    # Submit motion control task
    payload = {
        "model": KLING_MODELS["motion-control"],
        "input": {
            "image_url": img_url,
            "reference_video_url": ref_url,
            "duration": str(duration),
            "character_orientation": character_orientation,
        },
    }
    if prompt:
        payload["input"]["prompt"] = prompt

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        raise Exception(f"Kling submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    if not task_id:
        raise Exception(f"No task ID: {task_data}")

    print(f"[Kling] Task: {task_id}")

    # Poll until done
    result = _poll_task(task_id)
    return _extract_video(result)


def image_to_video(
    start_image_path: str,
    prompt: str,
    duration: int = 5,
) -> tuple[bytes, str]:
    """
    Generate video from a start image + prompt.

    Args:
        start_image_path: Path to start frame image
        prompt: Motion/action prompt
        duration: Video duration in seconds

    Returns:
        Tuple of (video_bytes, video_url)
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    img_url = _upload_file_to_evolink(start_image_path)

    payload = {
        "model": KLING_MODELS["image-to-video"],
        "input": {
            "image_url": img_url,
            "prompt": prompt,
            "duration": str(duration),
        },
    }

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        raise Exception(f"Kling submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    if not task_id:
        raise Exception(f"No task ID: {task_data}")

    print(f"[Kling] Image-to-video task: {task_id}")
    result = _poll_task(task_id)
    return _extract_video(result)


def text_to_video(
    prompt: str,
    duration: int = 5,
) -> tuple[bytes, str]:
    """
    Generate video from text prompt only (fallback).

    Args:
        prompt: Video generation prompt
        duration: Video duration in seconds

    Returns:
        Tuple of (video_bytes, video_url)
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": KLING_MODELS["text-to-video"],
        "input": {
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": "9:16",
        },
    }

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        raise Exception(f"Kling submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    if not task_id:
        raise Exception(f"No task ID: {task_data}")

    print(f"[Kling] Text-to-video task: {task_id}")
    result = _poll_task(task_id)
    return _extract_video(result)


def save_video(video_bytes: bytes, job_id: str, asset_index: int) -> str:
    """Save generated video to the job's generated directory."""
    gen_dir = JOBS_DIR / job_id / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    filename = f"asset_{asset_index:02d}_kling.mp4"
    filepath = gen_dir / filename
    filepath.write_bytes(video_bytes)
    size_mb = len(video_bytes) / (1024 * 1024)
    print(f"[Kling] Saved: {filepath} ({size_mb:.1f} MB)")
    return str(filepath)
