"""RunPod InfiniteTalk lip-sync: submit avatar + audio, get talking-head video."""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.request
from pathlib import Path

from config import RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, AVATAR_IMAGE_PATH, GENERATED_DIR

log = logging.getLogger(__name__)

# RunPod API settings
RUNPOD_BASE_URL = "https://api.runpod.ai/v2"
POLL_INTERVAL = 15  # seconds between status checks
MAX_WAIT = 900  # 15 minutes max wait


def _load_workflow() -> dict:
    """Load the InfiniteTalk ComfyUI workflow template."""
    workflow_path = Path(__file__).parent / "workflow_infinitetalk.json"
    if workflow_path.exists():
        with open(workflow_path) as f:
            return json.load(f)
    # Build minimal workflow inline
    return _build_default_workflow()


def _build_default_workflow() -> dict:
    """Build the default InfiniteTalk workflow for RunPod."""
    return {
        "input": {
            "workflow": {
                "5": {
                    "inputs": {"image": "avatar.png", "upload": "image"},
                    "class_type": "LoadImage",
                },
                "232": {
                    "inputs": {"audio": "audio.mp3", "upload": "audio"},
                    "class_type": "LoadAudio",
                },
                "281": {
                    "inputs": {
                        "width": 480,
                        "height": 480,
                        "batch_size": 1,
                    },
                    "class_type": "EmptyLatentImage",
                },
            }
        }
    }


def submit_lipsync_job(audio_path: str, avatar_path: str | None = None) -> str | None:
    """Submit a lip-sync job to RunPod.

    Args:
        audio_path: Path to TTS audio file (mp3)
        avatar_path: Path to avatar image (png). Uses config default if None.

    Returns:
        Job ID string, or None on error.
    """
    if not RUNPOD_API_KEY or not RUNPOD_ENDPOINT_ID:
        log.error("RunPod API key or endpoint not configured")
        return None

    avatar = avatar_path or AVATAR_IMAGE_PATH
    if not avatar or not Path(avatar).exists():
        log.error("Avatar image not found: %s", avatar)
        return None

    if not Path(audio_path).exists():
        log.error("Audio file not found: %s", audio_path)
        return None

    # Read and encode files
    with open(avatar, "rb") as f:
        avatar_b64 = base64.b64encode(f.read()).decode()
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    avatar_ext = Path(avatar).suffix.lstrip(".")
    audio_ext = Path(audio_path).suffix.lstrip(".")

    # Build payload with file inputs
    payload = {
        "input": {
            "images": [
                {
                    "name": f"avatar.{avatar_ext}",
                    "image": avatar_b64,
                }
            ],
            "audio": [
                {
                    "name": f"audio.{audio_ext}",
                    "audio": audio_b64,
                }
            ],
        }
    }

    # Load and merge workflow
    workflow = _load_workflow()
    if "input" in workflow and "workflow" in workflow["input"]:
        payload["input"]["workflow"] = workflow["input"]["workflow"]

    url = f"{RUNPOD_BASE_URL}/{RUNPOD_ENDPOINT_ID}/run"
    data = json.dumps(payload).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {RUNPOD_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        job_id = result.get("id")
        log.info("RunPod job submitted: %s", job_id)
        return job_id
    except Exception as e:
        log.error("RunPod job submission failed: %s", e)
        return None


def poll_job(job_id: str) -> dict | None:
    """Poll RunPod job until completion.

    Returns:
        Result dict with 'status' and 'output', or None on error.
    """
    url = f"{RUNPOD_BASE_URL}/{RUNPOD_ENDPOINT_ID}/status/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
    )

    start = time.time()
    while time.time() - start < MAX_WAIT:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            log.warning("Poll error (will retry): %s", e)
            time.sleep(POLL_INTERVAL)
            continue

        status = result.get("status", "UNKNOWN")
        elapsed = int(time.time() - start)
        log.info("[%ds] RunPod job %s: %s", elapsed, job_id, status)

        if status == "COMPLETED":
            return result
        elif status == "FAILED":
            log.error("RunPod job failed: %s", result.get("error", "unknown"))
            return None
        elif status in ("IN_QUEUE", "IN_PROGRESS"):
            time.sleep(POLL_INTERVAL)
        else:
            log.warning("Unknown RunPod status: %s", status)
            time.sleep(POLL_INTERVAL)

    log.error("RunPod job timed out after %ds", MAX_WAIT)
    return None


def save_lipsync_video(result: dict, output_filename: str | None = None) -> str | None:
    """Extract and save lip-sync video from RunPod result.

    Returns:
        Path to saved video file, or None on error.
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if output_filename is None:
        output_filename = f"lipsync_{int(time.time())}.mp4"
    output_path = GENERATED_DIR / output_filename

    output = result.get("output", {})
    images = output.get("images", [])

    for item in images:
        data = item.get("data", "")
        dtype = item.get("type", "")
        if dtype == "base64" and data:
            video_bytes = base64.b64decode(data)
            output_path.write_bytes(video_bytes)
            size_mb = len(video_bytes) / 1024 / 1024
            log.info("Lip-sync video saved: %s (%.1fMB)", output_path, size_mb)
            return str(output_path)

    log.error("No video data found in RunPod result")
    return None


def generate_lipsync_video(audio_path: str, avatar_path: str | None = None) -> str | None:
    """Full pipeline: submit job, wait for completion, save video.

    Args:
        audio_path: Path to TTS audio file
        avatar_path: Path to avatar image (uses config default if None)

    Returns:
        Path to lip-sync video file, or None on error.
    """
    log.info("Starting lip-sync generation...")

    # Submit job
    job_id = submit_lipsync_job(audio_path, avatar_path)
    if not job_id:
        return None

    # Poll until done
    result = poll_job(job_id)
    if not result:
        return None

    # Save video
    return save_lipsync_video(result)
