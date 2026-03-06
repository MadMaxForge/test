import uuid
import base64
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import JobStatus, JobResponse
from app.services import elevenlabs, s3_storage, runpod_api


# In-memory job store (for MVP; can migrate to SQLite later)
_jobs: dict[str, dict] = {}


def _create_job(text: str, voice_id: Optional[str] = None) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": JobStatus.QUEUED,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "voice_id": voice_id,
        "audio_url": None,
        "video_url": None,
        "error": None,
        "runpod_job_id": None,
    }
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


def update_job(job_id: str, **kwargs: object) -> None:
    if job_id in _jobs:
        _jobs[job_id].update(kwargs)


async def generate_video(
    job_id: str,
    text: str,
    image_bytes: bytes,
    image_filename: str,
    voice_id: Optional[str] = None,
    model_id: str = "eleven_multilingual_v2",
) -> None:
    """Full pipeline: text -> TTS -> upload -> RunPod ComfyUI -> video.
    
    This runs as a background task.
    """
    try:
        # Step 1: Generate audio via ElevenLabs
        update_job(job_id, status=JobStatus.GENERATING_AUDIO)
        audio_bytes = await elevenlabs.text_to_speech(text, voice_id, model_id)

        # Step 2: Upload audio and image to S3
        update_job(job_id, status=JobStatus.UPLOADING)
        audio_key = f"jobs/{job_id}/audio.mp3"
        image_key = f"jobs/{job_id}/{image_filename}"

        audio_url = s3_storage.upload_file(audio_bytes, audio_key, "audio/mpeg")
        image_url = s3_storage.upload_file(image_bytes, image_key, "image/jpeg")

        update_job(job_id, audio_url=audio_url)

        # Step 3: Submit ComfyUI workflow to RunPod
        update_job(job_id, status=JobStatus.GENERATING_VIDEO)

        # Encode image as base64 for ComfyUI workflow input
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Build a basic ComfyUI workflow for lip-sync
        # This is a template — the actual workflow depends on which
        # nodes/models are installed on the RunPod worker
        workflow = _build_lipsync_workflow(audio_url, image_b64)

        result = await runpod_api.submit_comfyui_job(
            workflow=workflow,
            images={"input_face.jpg": image_b64},
        )

        runpod_job_id = result.get("id")
        update_job(job_id, runpod_job_id=runpod_job_id)

    except ValueError as e:
        # RunPod endpoint not configured — store audio result only
        update_job(
            job_id,
            status=JobStatus.COMPLETED,
            error=f"Video generation skipped: {e}. Audio was generated successfully.",
        )
    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


async def poll_runpod_status(job_id: str) -> Optional[str]:
    """Poll RunPod for job status. Returns video URL if completed."""
    job = get_job(job_id)
    if not job or not job.get("runpod_job_id"):
        return None

    try:
        result = await runpod_api.check_job_status(job["runpod_job_id"])
        status = result.get("status", "")

        if status == "COMPLETED":
            output = result.get("output", {})
            video_url = None
            # worker-comfyui returns images as base64 or S3 URLs
            if isinstance(output, dict):
                video_url = output.get("video_url") or output.get("message")
            elif isinstance(output, str):
                video_url = output
            update_job(job_id, status=JobStatus.COMPLETED, video_url=video_url)
            return video_url

        elif status == "FAILED":
            error = result.get("error", "RunPod job failed")
            update_job(job_id, status=JobStatus.FAILED, error=str(error))

        # Still in progress
        return None

    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=f"Polling error: {e}")
        return None


def create_job_entry(text: str, voice_id: Optional[str] = None) -> str:
    """Public interface to create a new job entry."""
    return _create_job(text, voice_id)


def _build_lipsync_workflow(audio_url: str, image_b64: str) -> dict:
    """Build a ComfyUI API workflow for lip-sync generation.
    
    NOTE: This is a placeholder template. The actual workflow JSON
    depends on which custom nodes (LatentSync, InfiniteTalk, etc.)
    are installed on the RunPod ComfyUI worker.
    
    The workflow should be exported from ComfyUI in API format
    and customized with the correct node IDs.
    """
    return {
        "prompt": {
            "1": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": "input_face.jpg",
                },
            },
            "2": {
                "class_type": "LoadAudio",
                "inputs": {
                    "audio": audio_url,
                },
            },
            "3": {
                "class_type": "LatentSyncNode",
                "inputs": {
                    "images": ["1", 0],
                    "audio": ["2", 0],
                },
            },
            "4": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["3", 0],
                    "audio": ["2", 0],
                    "frame_rate": 25,
                    "format": "video/h264-mp4",
                },
            },
        }
    }
