import httpx
import os
from typing import Optional


ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel - default female voice


def _get_api_key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "")
    if not key:
        raise ValueError("ELEVENLABS_API_KEY not set")
    return key


async def list_voices() -> list[dict]:
    """List all available ElevenLabs voices."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{ELEVENLABS_BASE_URL}/voices",
            headers={"xi-api-key": _get_api_key()},
        )
        resp.raise_for_status()
        data = resp.json()
        voices = []
        for v in data.get("voices", []):
            voices.append({
                "voice_id": v["voice_id"],
                "name": v["name"],
                "category": v.get("category"),
                "description": v.get("labels", {}).get("description"),
                "preview_url": v.get("preview_url"),
            })
        return voices


async def text_to_speech(
    text: str,
    voice_id: Optional[str] = None,
    model_id: str = "eleven_multilingual_v2",
) -> bytes:
    """Convert text to speech using ElevenLabs API. Returns audio bytes (mp3)."""
    if not voice_id:
        voice_id = DEFAULT_VOICE_ID

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": _get_api_key(),
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": True,
                },
            },
        )
        resp.raise_for_status()
        return resp.content
