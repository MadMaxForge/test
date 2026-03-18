"""Text-to-Speech: ElevenLabs or edge-tts fallback."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import aiohttp

from config import (
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
    TTS_PROVIDER, EDGE_TTS_VOICE, GENERATED_DIR,
)

log = logging.getLogger(__name__)


async def generate_tts(text: str, output_filename: str | None = None) -> str | None:
    """Generate TTS audio from text.
    
    Returns path to generated audio file or None on error.
    """
    if TTS_PROVIDER == "elevenlabs":
        return await _elevenlabs_tts(text, output_filename)
    else:
        return await _edge_tts(text, output_filename)


async def _elevenlabs_tts(text: str, output_filename: str | None = None) -> str | None:
    """Generate TTS using ElevenLabs API."""
    if not ELEVENLABS_API_KEY:
        log.error("ELEVENLABS_API_KEY not configured")
        return None
    
    voice_id = ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"  # Default: Rachel
    
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        output_filename = f"tts_{int(time.time())}.mp3"
    output_path = GENERATED_DIR / output_filename
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.8,
            "style": 0.3,
            "use_speaker_boost": True,
        },
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log.error("ElevenLabs API error %d: %s", resp.status, error_text[:300])
                    return None
                
                audio_data = await resp.read()
                output_path.write_bytes(audio_data)
                log.info("ElevenLabs TTS generated: %s (%d bytes)", output_path, len(audio_data))
                return str(output_path)
    except Exception as e:
        log.error("ElevenLabs TTS failed: %s", e)
        return None


async def _edge_tts(text: str, output_filename: str | None = None) -> str | None:
    """Generate TTS using free edge-tts (Microsoft)."""
    try:
        import edge_tts
    except ImportError:
        log.error("edge-tts not installed: pip install edge-tts")
        return None
    
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        output_filename = f"tts_{int(time.time())}.mp3"
    output_path = GENERATED_DIR / output_filename
    
    try:
        communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
        await communicate.save(str(output_path))
        log.info("Edge TTS generated: %s", output_path)
        return str(output_path)
    except Exception as e:
        log.error("Edge TTS failed: %s", e)
        return None


async def list_elevenlabs_voices() -> list[dict]:
    """List available ElevenLabs voices (for setup)."""
    if not ELEVENLABS_API_KEY:
        return []
    
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return [
                    {"voice_id": v["voice_id"], "name": v["name"], "labels": v.get("labels", {})}
                    for v in data.get("voices", [])
                ]
    except Exception:
        return []
