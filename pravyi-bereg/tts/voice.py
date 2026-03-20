"""Text-to-Speech: ElevenLabs with SSML pauses + word-level timestamps, or edge-tts fallback."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path

import aiohttp

from config import (
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
    TTS_PROVIDER, EDGE_TTS_VOICE, GENERATED_DIR,
)

log = logging.getLogger(__name__)


def add_ssml_pauses(text: str) -> str:
    """Add SSML pause tags after punctuation for more natural speech."""
    text = re.sub(r'\.(\s)', r'. <break time="600ms"/>\1', text)
    text = re.sub(r',(\s)', r', <break time="400ms"/>\1', text)
    text = re.sub(r'([?!])(\s)', r'\1 <break time="500ms"/>\2', text)
    text = re.sub(r'([;:])(\s)', r'\1 <break time="400ms"/>\2', text)
    return text


async def generate_tts(text: str, output_filename: str | None = None) -> str | None:
    """Generate TTS audio from text. Returns path to generated audio file."""
    if TTS_PROVIDER == "elevenlabs":
        return await _elevenlabs_tts(text, output_filename)
    else:
        return await _edge_tts(text, output_filename)


async def generate_tts_with_timestamps(
    text: str,
    output_filename: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Generate TTS audio AND word-level timestamps.

    Returns (audio_path, word_timestamps) where word_timestamps is a list of
    {'word': str, 'start': float, 'end': float}.
    """
    if TTS_PROVIDER == "elevenlabs":
        return await _elevenlabs_tts_with_timestamps(text, output_filename)
    else:
        path = await _edge_tts(text, output_filename)
        return path, []


async def _elevenlabs_tts(text: str, output_filename: str | None = None) -> str | None:
    """Generate TTS using ElevenLabs API (simple, no timestamps)."""
    if not ELEVENLABS_API_KEY:
        log.error("ELEVENLABS_API_KEY not configured")
        return None

    voice_id = ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"
    text_with_pauses = add_ssml_pauses(text)

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
        "text": text_with_pauses,
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


async def _elevenlabs_tts_with_timestamps(
    text: str,
    output_filename: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Generate TTS with word-level timestamps using ElevenLabs with-timestamps endpoint."""
    if not ELEVENLABS_API_KEY:
        log.error("ELEVENLABS_API_KEY not configured")
        return None, []

    voice_id = ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"
    text_with_pauses = add_ssml_pauses(text)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        output_filename = f"tts_{int(time.time())}.mp3"
    output_path = GENERATED_DIR / output_filename
    timestamps_path = GENERATED_DIR / output_filename.replace(".mp3", "_words.json")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text_with_pauses,
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
                    log.error("ElevenLabs timestamps API error %d: %s", resp.status, error_text[:300])
                    path = await _elevenlabs_tts(text, output_filename)
                    return path, []
                data = await resp.json()

        # Extract audio (base64)
        audio_b64 = data.get("audio_base64", "")
        if audio_b64:
            output_path.write_bytes(base64.b64decode(audio_b64))
            log.info("ElevenLabs TTS+timestamps generated: %s", output_path)

        # Extract word-level alignment
        alignment = data.get("alignment", {})
        characters = alignment.get("characters", [])
        char_starts = alignment.get("character_start_times_seconds", [])
        char_ends = alignment.get("character_end_times_seconds", [])

        words = _chars_to_words(characters, char_starts, char_ends)
        clean_words = _filter_ssml_words(words)

        with open(timestamps_path, "w", encoding="utf-8") as f:
            json.dump(clean_words, f, ensure_ascii=False, indent=2)
        log.info("Saved %d word timestamps to %s", len(clean_words), timestamps_path)

        return str(output_path), clean_words

    except Exception as e:
        log.error("ElevenLabs TTS+timestamps failed: %s", e)
        path = await _elevenlabs_tts(text, output_filename)
        return path, []


def _chars_to_words(
    characters: list[str],
    starts: list[float],
    ends: list[float],
) -> list[dict]:
    """Convert character-level alignment to word-level timestamps."""
    words = []
    current_word = ""
    word_start = 0.0

    for i, char in enumerate(characters):
        if i >= len(starts) or i >= len(ends):
            break
        if char in (" ", "\n"):
            if current_word:
                words.append({
                    "word": current_word,
                    "start": round(word_start, 3),
                    "end": round(ends[i - 1] if i > 0 else ends[i], 3),
                })
                current_word = ""
        else:
            if not current_word:
                word_start = starts[i]
            current_word += char

    if current_word:
        words.append({
            "word": current_word,
            "start": round(word_start, 3),
            "end": round(ends[-1] if ends else 0, 3),
        })
    return words


def _filter_ssml_words(words: list[dict]) -> list[dict]:
    """Remove SSML artifacts (like <break ...>) from word list."""
    clean = []
    for w in words:
        word = w["word"]
        if word.startswith("<") or word.endswith("/>") or word.startswith("time="):
            continue
        if "break" in word and len(word) < 10:
            continue
        if re.match(r'^"\d+ms"$', word):
            continue
        if not word.strip():
            continue
        clean.append(w)
    return clean


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
