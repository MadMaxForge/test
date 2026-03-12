from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException
from typing import Optional

from app.models.scail_schemas import ScailJobResponse, ScailJobListResponse, ScailJobStatus
from app.services import scail_job_manager

router = APIRouter(prefix="/api", tags=["motion-control"])


@router.post("/generate-motion", response_model=ScailJobResponse)
async def generate_motion_video(
    background_tasks: BackgroundTasks,
    prompt: str = Form(..., description="Text prompt describing the desired output"),
    video: UploadFile = File(..., description="Reference video for motion tracking"),
    image: UploadFile = File(..., description="Character reference image"),
    negative_prompt: Optional[str] = Form(None, description="Negative prompt"),
    resolution: int = Form(832, description="Target resolution (320, 640, 832, 960, 1280)"),
):
    """Start a SCAIL motion control video generation job.

    Accepts a reference video (for motion/skeleton tracking), a character
    reference image, and a text prompt. Returns a video where the character
    performs the motion from the reference video.

    Uses WAN SCAIL for motion tracking and Flux Klein for first frame generation.
    """
    # Validate video file
    if video.content_type and not video.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")

    # Validate image file
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # Validate resolution
    valid_resolutions = [320, 640, 832, 960, 1280]
    if resolution not in valid_resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"Resolution must be one of {valid_resolutions}",
        )

    video_bytes = await video.read()
    if len(video_bytes) > 100 * 1024 * 1024:  # 100MB limit
        raise HTTPException(status_code=400, detail="Video too large (max 100MB)")

    image_bytes = await image.read()
    if len(image_bytes) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")

    # Create job entry
    job_id = scail_job_manager.create_scail_job_entry(
        prompt=prompt,
        negative_prompt=negative_prompt,
        resolution=resolution,
    )

    # Run pipeline in background
    background_tasks.add_task(
        scail_job_manager.generate_scail_video,
        job_id=job_id,
        prompt=prompt,
        video_bytes=video_bytes,
        video_filename=video.filename or "reference.mp4",
        image_bytes=image_bytes,
        image_filename=image.filename or "character.png",
        negative_prompt=negative_prompt,
        resolution=resolution,
    )

    job = scail_job_manager.get_scail_job(job_id)
    return ScailJobResponse(**job)


@router.get("/motion-jobs/{job_id}", response_model=ScailJobResponse)
async def get_motion_job(job_id: str):
    """Get the status of a SCAIL motion control job."""
    job = scail_job_manager.get_scail_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # If job is generating video, poll RunPod for status
    if job["status"] == ScailJobStatus.GENERATING_VIDEO and job.get("runpod_job_id"):
        await scail_job_manager.poll_scail_runpod_status(job_id)
        job = scail_job_manager.get_scail_job(job_id)

    return ScailJobResponse(**job)


@router.get("/motion-jobs", response_model=ScailJobListResponse)
async def list_motion_jobs():
    """List all SCAIL motion control jobs."""
    jobs = scail_job_manager.list_scail_jobs()
    return ScailJobListResponse(
        jobs=[ScailJobResponse(**j) for j in jobs],
        total=len(jobs),
    )
