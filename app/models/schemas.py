from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING_AUDIO = "generating_audio"
    UPLOADING = "uploading"
    GENERATING_VIDEO = "generating_video"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="Text to convert to speech")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID. If not provided, uses default.")
    model_id: Optional[str] = Field("eleven_multilingual_v2", description="ElevenLabs model ID")


class TTSPreviewRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice_id: Optional[str] = None
    model_id: Optional[str] = "eleven_multilingual_v2"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    text: str
    voice_id: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    runpod_job_id: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    preview_url: Optional[str] = None


class VoiceListResponse(BaseModel):
    voices: list[VoiceInfo]
