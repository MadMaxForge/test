from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ScailJobStatus(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    GENERATING_VIDEO = "generating_video"
    COMPLETED = "completed"
    FAILED = "failed"


class ScailJobResponse(BaseModel):
    job_id: str
    status: ScailJobStatus
    created_at: str
    prompt: str
    negative_prompt: Optional[str] = None
    resolution: int = 832
    video_url: Optional[str] = None
    error: Optional[str] = None
    runpod_job_id: Optional[str] = None


class ScailJobListResponse(BaseModel):
    jobs: list[ScailJobResponse]
    total: int
