"""
Central configuration for the Content Creation Module.

All environment variables, paths, constants, and model settings live here.
No module should read env vars directly — import from config instead.
"""

import os
from pathlib import Path


# ── Workspace paths ──────────────────────────────────────────────
WORKSPACE = Path(os.environ.get("CONTENT_WORKSPACE", "/root/.openclaw/workspace"))
DATA_DIR = WORKSPACE / "data"
JOBS_DIR = DATA_DIR / "jobs"
QUEUE_DIR = DATA_DIR / "queue"
DOWNLOADS_DIR = DATA_DIR / "downloads"
OUTPUT_DIR = WORKSPACE / "output"

# Ensure directories exist on import
for _d in (DATA_DIR, JOBS_DIR, QUEUE_DIR, DOWNLOADS_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── API keys ─────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
EVOLINK_API_KEY = os.environ.get("EVOLINK_API_KEY", "")

# ── ComfyUI (Z-Image via SSH tunnel) ────────────────────────────
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8001")

# ── LLM models ───────────────────────────────────────────────────
# Scout/Analyst, QC, Publish — fast & cheap
MODEL_ANALYST = os.environ.get("MODEL_ANALYST", "google/gemini-2.0-flash-lite-001")
MODEL_PUBLISH = os.environ.get("MODEL_PUBLISH", "google/gemini-2.0-flash-lite-001")
# Planner, Creative — stronger reasoning
MODEL_PLANNER = os.environ.get("MODEL_PLANNER", "moonshotai/kimi-k2.5")
MODEL_CREATIVE = os.environ.get("MODEL_CREATIVE", "moonshotai/kimi-k2.5")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ── Evolink (Nano Banana + Kling) ────────────────────────────────
EVOLINK_BASE_URL = "https://api.evolink.ai/v1"
EVOLINK_FILES_URL = "https://files-api.evolink.ai"
NANO_BANANA_MODEL = "nano-banana-2-beta"

KLING_MODELS = {
    "text-to-video": "kling-v3-text-to-video",
    "image-to-video": "kling-v3-image-to-video",
    "motion-control": "kling-v3-motion-control",
}

# ── Instagram content dimensions ─────────────────────────────────
DIMENSIONS = {
    "post": (1080, 1350),    # 4:5
    "story": (1088, 1920),   # 9:16
    "reel": (1088, 1920),    # 9:16
}

ASPECT_RATIOS = {
    "post": "4:5",
    "story": "9:16",
    "reel": "9:16",
}

# ── Content limits (Phase 1) ────────────────────────────────────
MAX_POST_SLIDES = 4
MAX_STORY_FRAMES = 4
CHARACTER_RATIO_TARGET = 0.65  # ~65% character, ~35% world

# ── Job states ───────────────────────────────────────────────────
JOB_STATES = [
    "draft",
    "analyzing",
    "planning",
    "prompting",
    "generating",
    "review",
    "revising",
    "text_review",
    "approved",
    "failed",
]

ASSET_STATES = [
    "pending",
    "generating",
    "generated",
    "approved",
    "rejected",
    "failed",
]

# ── Timeouts (seconds) ──────────────────────────────────────────
TIMEOUTS = {
    "analyst": 180,
    "planner": 60,
    "creative": 120,
    "z_image": 900,
    "nano_banana": 600,
    "kling": 600,
    "publish": 60,
}

# ── Z-Image prompt rules ────────────────────────────────────────
ZIMAGE_TRIGGER = "A w1man,"
ZIMAGE_PROMPT_TEMPLATE = """{trigger} {setting}
Background: {background}
{appearance}
Outfit ({outfit_name}): {outfit_description}
{camera_and_lighting}"""

# Character appearance defaults (can be overridden per-job)
DEFAULT_CHARACTER = {
    "trigger": ZIMAGE_TRIGGER,
    "appearance": (
        "She has long, straight black hair cascading down past her shoulders. "
        "Her expression is natural and confident, with soft natural makeup."
    ),
}
