from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException
from typing import Optional

from app.models.schemas import JobResponse, JobListResponse, GenerateRequest
from app.services import job_manager

router = APIRouter(prefix="/api", tags=["generation"])


@router.post("/generate", response_model=JobResponse)
async def generate_video(
    background_tasks: BackgroundTasks,
    text: str = Form(...),
    image: UploadFile = File(...),
    voice_id: Optional[str] = Form(None),
    model_id: Optional[str] = Form("eleven_multilingual_v2"),
):
    """Start a video generation job: text + image -> lip-sync video."""
    # Validate file type
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await image.read()
    if len(image_bytes) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")

    # Create job entry
    job_id = job_manager.create_job_entry(text, voice_id)

    # Run pipeline in background
    background_tasks.add_task(
        job_manager.generate_video,
        job_id=job_id,
        text=text,
        image_bytes=image_bytes,
        image_filename=image.filename or "face.jpg",
        voice_id=voice_id,
        model_id=model_id or "eleven_multilingual_v2",
        image_content_type=image.content_type or "image/jpeg",
    )

    job = job_manager.get_job(job_id)
    return JobResponse(**job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get the status of a generation job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # If job is in video generation state, poll RunPod
    from app.models.schemas import JobStatus
    if job["status"] == JobStatus.GENERATING_VIDEO and job.get("runpod_job_id"):
        await job_manager.poll_runpod_status(job_id)
        job = job_manager.get_job(job_id)

    return JobResponse(**job)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs():
    """List all generation jobs."""
    jobs = job_manager.list_jobs()
    return JobListResponse(
        jobs=[JobResponse(**j) for j in jobs],
        total=len(jobs),
    )
