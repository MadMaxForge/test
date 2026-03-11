"""Pydantic schemas for EvoLink-powered endpoints (carousel images & reels)."""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


# ── Shared ─────────────────────────────────────────────────────────


class EvoLinkTaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Carousel (Nano Banana 2 — Image Generation) ───────────────────


class CarouselImageRequest(BaseModel):
    """Request to generate a lifestyle/carousel image via Nano Banana 2.

    Used by agents to create atmospheric photos for Instagram carousels:
    coffee shops, makeup, branded accessories, etc.
    """

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Description of the image to generate, e.g. "
        "'A flat lay of luxury makeup products on a marble table, "
        "soft natural lighting, Instagram aesthetic'",
    )
    size: str = Field(
        "4:5",
        description="Aspect ratio. Common for Instagram: "
        "'4:5' (portrait post), '1:1' (square), '9:16' (story/reel cover), '16:9' (landscape).",
    )
    quality: str = Field(
        "1K",
        description="Image resolution: 0.5K, 1K, 2K, 4K. "
        "1K is sufficient for Instagram, 2K for print-quality.",
    )
    reference_image_urls: Optional[list[str]] = Field(
        None,
        description="Optional reference image URLs for style/composition guidance. "
        "Max 14 images, each ≤20MB.",
    )


class CarouselImageResponse(BaseModel):
    """Response for a carousel image generation task."""

    task_id: str
    status: EvoLinkTaskStatus
    created_at: str
    prompt: str
    size: str
    quality: str
    image_urls: Optional[list[str]] = None
    error: Optional[str] = None


class CarouselImageListResponse(BaseModel):
    tasks: list[CarouselImageResponse]
    total: int


# ── Reels (Kling V3 Motion Control — Video Generation) ────────────


class ReelRequest(BaseModel):
    """Request to generate a reel via Kling V3 Motion Control.

    The agent provides:
    - character_image_url: URL of the character image (generated via ComfyUI + LoRA)
    - reference_video_url: URL of a trending dance/reel video to transfer motion from
    - prompt: Optional scene/environment description
    """

    character_image_url: str = Field(
        ...,
        description="URL of the character reference image (generated via ComfyUI + LoRA). "
        "JPG/PNG, ≤10MB, min 300px, aspect ratio between 1:2.5 and 2.5:1.",
    )
    reference_video_url: str = Field(
        ...,
        description="URL of the motion reference video (e.g. a trending dance reel). "
        "MP4/MOV, 3–30 seconds, ≤100MB.",
    )
    prompt: Optional[str] = Field(
        None,
        max_length=2500,
        description="Optional text guidance for the scene/environment. "
        "Describe context, not motion (motion comes from the reference video). "
        "E.g. 'A stylish girl dancing in a modern apartment, warm lighting'.",
    )
    character_orientation: str = Field(
        "video",
        description="'video': match orientation from reference video (max 30s). "
        "'image': match orientation from character image (max 10s, better for camera movements).",
    )
    quality: str = Field(
        "720p",
        description="Output resolution: '720p' (standard, cheaper) or '1080p' (pro).",
    )
    keep_sound: bool = Field(
        False,
        description="Keep original audio from the reference video.",
    )


class ReelResponse(BaseModel):
    """Response for a reel generation task."""

    task_id: str
    status: EvoLinkTaskStatus
    created_at: str
    character_image_url: str
    reference_video_url: str
    prompt: Optional[str] = None
    quality: str
    video_url: Optional[str] = None
    error: Optional[str] = None


class ReelListResponse(BaseModel):
    tasks: list[ReelResponse]
    total: int
