"""Configuration from .env file."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
MEDIA_DIR = BASE_DIR / "media"
SOURCE_PHOTOS = MEDIA_DIR / "source" / "photos"
SOURCE_VIDEOS = MEDIA_DIR / "source" / "videos"
GENERATED_DIR = MEDIA_DIR / "generated"
MUSIC_DIR = MEDIA_DIR / "music"
FONTS_DIR = MEDIA_DIR / "templates" / "fonts"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "pravyi_bereg.db"

# VK
VK_COMMUNITY_TOKEN = os.getenv("VK_COMMUNITY_TOKEN", "")
VK_USER_TOKEN = os.getenv("VK_USER_TOKEN", "")
VK_COMMUNITY_ID = int(os.getenv("VK_COMMUNITY_ID", "236779093"))

# OpenRouter (replaces direct OpenAI)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ElevenLabs TTS
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs")  # elevenlabs or edge-tts
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "ru-RU-DmitryNeural")

# RunPod (InfiniteTalk lip-sync)
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID", "")
AVATAR_IMAGE_PATH = os.getenv("AVATAR_IMAGE_PATH", "")  # Path to avatar PNG for lip-sync

# Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_OWNER_CHAT_ID = int(os.getenv("TG_OWNER_CHAT_ID", "0"))

# Schedule (MSK)
TIMEZONE = "Europe/Moscow"

# Brand
BRAND_NAME = "Правый Берег"
BRAND_DESCRIPTION = "Недвижимость и земельные участки в Нижегородской области"
BRAND_REGION = "Городецкий, Сокольский, Чкаловский районы, Нижний Новгород"
BRAND_EXPERIENCE = "30+ лет опыта"
BRAND_PHONE = ""  # Will be set later
BRAND_VK_URL = "https://vk.com/club236779093"

# Content settings
MAX_POST_LENGTH = 2000
MIN_POST_LENGTH = 400
MAX_REEL_SCRIPT_LENGTH = 500
HASHTAGS_COUNT = 5
REEL_TARGET_WORDS = (45, 65)  # min/max words for ~20-25s reel narration (faster lip-sync)
REEL_AUDIO_BUFFER = 0.5  # seconds to add after last word for smooth ending

# Brand colors (emerald green theme)
BRAND_COLOR_PRIMARY = (0, 128, 85)  # Emerald green
BRAND_COLOR_DARK = (0, 77, 51)  # Dark emerald
BRAND_COLOR_ACCENT = (0, 179, 119)  # Light emerald


def ensure_dirs():
    """Create all required directories."""
    for d in (SOURCE_PHOTOS, SOURCE_VIDEOS, GENERATED_DIR, MUSIC_DIR,
              FONTS_DIR, LOGS_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def validate_config() -> list[str]:
    """Check required config values. Returns list of warnings."""
    warnings = []
    if not VK_COMMUNITY_TOKEN:
        warnings.append("VK_COMMUNITY_TOKEN not set")
    if not VK_USER_TOKEN:
        warnings.append("VK_USER_TOKEN not set")
    if not OPENROUTER_API_KEY:
        warnings.append("OPENROUTER_API_KEY not set")
    if not TG_BOT_TOKEN:
        warnings.append("TG_BOT_TOKEN not set")
    if not TG_OWNER_CHAT_ID:
        warnings.append("TG_OWNER_CHAT_ID not set")
    if TTS_PROVIDER == "elevenlabs" and not ELEVENLABS_API_KEY:
        warnings.append("ELEVENLABS_API_KEY not set (TTS_PROVIDER=elevenlabs)")
    if not RUNPOD_API_KEY:
        warnings.append("RUNPOD_API_KEY not set (needed for lip-sync reels)")
    if not RUNPOD_ENDPOINT_ID:
        warnings.append("RUNPOD_ENDPOINT_ID not set")
    if not AVATAR_IMAGE_PATH:
        warnings.append("AVATAR_IMAGE_PATH not set (needed for lip-sync)")
    return warnings
