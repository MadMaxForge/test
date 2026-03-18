"""Video builder: assemble reels from source video + TTS audio + subtitles."""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from config import GENERATED_DIR, SOURCE_VIDEOS, MUSIC_DIR

log = logging.getLogger(__name__)

# Video output settings
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920  # Vertical (9:16) for reels
VIDEO_FPS = 30
VIDEO_BITRATE = "4M"

# Subtitle settings
SUBTITLE_FONT = "DejaVu Sans"
SUBTITLE_FONTSIZE = 48
SUBTITLE_COLOR = "white"
SUBTITLE_OUTLINE = 2
SUBTITLE_POSITION = "center"


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        log.error("Failed to get video duration: %s", e)
        return 0.0


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    return get_video_duration(audio_path)  # ffprobe works for audio too


def select_video_fragments(
    audio_duration: float,
    scenes: list[dict],
    fragment_count: int = 4,
) -> list[dict]:
    """Select video fragments to match audio duration.
    
    Each fragment gets a proportional duration based on scene descriptions.
    """
    source_videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))
    
    if not source_videos:
        log.warning("No source videos found in %s", SOURCE_VIDEOS)
        return []
    
    # Distribute time evenly across fragments
    fragment_duration = audio_duration / fragment_count
    
    fragments = []
    for i in range(fragment_count):
        video_file = source_videos[i % len(source_videos)]
        video_dur = get_video_duration(str(video_file))
        
        if video_dur <= 0:
            continue
        
        # Random start point (not too close to end)
        import random
        max_start = max(0, video_dur - fragment_duration - 1)
        start_time = random.uniform(0, max_start) if max_start > 0 else 0
        
        fragments.append({
            "source": str(video_file),
            "start": round(start_time, 2),
            "duration": round(min(fragment_duration, video_dur - start_time), 2),
            "scene_index": i,
        })
    
    return fragments


def build_reel(
    audio_path: str,
    fragments: list[dict],
    subtitle_text: str = "",
    output_filename: str | None = None,
    background_music_path: str | None = None,
    music_volume: float = 0.1,
) -> str | None:
    """Assemble a reel video from fragments + audio + subtitles.
    
    Args:
        audio_path: Path to TTS audio file
        fragments: List of video fragment dicts from select_video_fragments
        subtitle_text: Full text for subtitle overlay
        output_filename: Output filename
        background_music_path: Optional background music
        music_volume: Volume of background music (0.0-1.0)
        
    Returns:
        Path to output video or None on error
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    
    if not fragments:
        log.error("No video fragments to build reel from")
        return None
    
    audio_duration = get_audio_duration(audio_path)
    if audio_duration <= 0:
        log.error("Invalid audio duration")
        return None
    
    if output_filename is None:
        output_filename = f"reel_{int(time.time())}.mp4"
    
    output_path = GENERATED_DIR / output_filename
    temp_concat = GENERATED_DIR / f"concat_{int(time.time())}.txt"
    temp_parts = []
    
    try:
        # Step 1: Prepare each fragment (resize to vertical 9:16)
        for i, frag in enumerate(fragments):
            temp_part = GENERATED_DIR / f"part_{int(time.time())}_{i}.mp4"
            temp_parts.append(str(temp_part))
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(frag["start"]),
                "-i", frag["source"],
                "-t", str(frag["duration"]),
                "-vf", (
                    f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                    "setsar=1"
                ),
                "-c:v", "libx264", "-preset", "fast",
                "-b:v", VIDEO_BITRATE,
                "-r", str(VIDEO_FPS),
                "-an",  # No audio yet
                "-movflags", "+faststart",
                str(temp_part),
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                log.error("Fragment %d encoding failed: %s", i, result.stderr[:500])
                return None
        
        # Step 2: Concatenate fragments
        with open(temp_concat, "w") as f:
            for part in temp_parts:
                f.write(f"file '{part}'\n")
        
        temp_video = GENERATED_DIR / f"concat_{int(time.time())}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(temp_concat),
            "-c", "copy",
            str(temp_video),
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log.error("Concatenation failed: %s", result.stderr[:500])
            return None
        
        # Step 3: Add audio + optional subtitles
        filter_complex_parts = []
        inputs = ["-i", str(temp_video), "-i", audio_path]
        
        # Audio mixing
        if background_music_path and Path(background_music_path).exists():
            inputs.extend(["-i", background_music_path])
            filter_complex_parts.append(
                f"[1:a]volume=1.0[voice];[2:a]volume={music_volume}[music];"
                "[voice][music]amix=inputs=2:duration=first[aout]"
            )
            audio_map = "[aout]"
        else:
            audio_map = "1:a"
        
        # Build final command
        cmd = ["ffmpeg", "-y"] + inputs
        
        if subtitle_text:
            # Generate SRT file from text
            srt_path = _generate_srt(subtitle_text, audio_duration)
            if srt_path:
                vf = (
                    f"subtitles={srt_path}:force_style='"
                    f"FontName={SUBTITLE_FONT},"
                    f"FontSize={SUBTITLE_FONTSIZE},"
                    f"PrimaryColour=&H00FFFFFF,"
                    f"OutlineColour=&H00000000,"
                    f"Outline={SUBTITLE_OUTLINE},"
                    f"Alignment=2,"
                    f"MarginV=100'"
                )
                cmd.extend(["-vf", vf])
        
        if filter_complex_parts:
            cmd.extend(["-filter_complex", ";".join(filter_complex_parts)])
            cmd.extend(["-map", "0:v", "-map", audio_map])
        else:
            cmd.extend(["-map", "0:v", "-map", audio_map])
        
        cmd.extend([
            "-c:v", "libx264", "-preset", "fast",
            "-b:v", VIDEO_BITRATE,
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("Final assembly failed: %s", result.stderr[:500])
            return None
        
        log.info("Reel built: %s (%.1fs)", output_path, get_video_duration(str(output_path)))
        return str(output_path)
    
    finally:
        # Cleanup temp files
        for f in temp_parts:
            Path(f).unlink(missing_ok=True)
        temp_concat.unlink(missing_ok=True)
        temp_video_path = GENERATED_DIR / f"concat_{int(time.time())}.mp4"
        if temp_video_path.exists():
            temp_video_path.unlink(missing_ok=True)


def _generate_srt(text: str, duration: float) -> str | None:
    """Generate SRT subtitle file from text, splitting into chunks."""
    words = text.split()
    if not words:
        return None
    
    srt_path = GENERATED_DIR / f"subs_{int(time.time())}.srt"
    
    # Split text into subtitle chunks (3-5 words each)
    chunks = []
    chunk_size = 4
    for i in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[i:i + chunk_size]))
    
    # Distribute time evenly
    chunk_duration = duration / len(chunks) if chunks else 1.0
    
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            start = i * chunk_duration
            end = (i + 1) * chunk_duration
            
            f.write(f"{i + 1}\n")
            f.write(f"{_format_time(start)} --> {_format_time(end)}\n")
            f.write(f"{chunk}\n\n")
    
    return str(srt_path)


def _format_time(seconds: float) -> str:
    """Format seconds as SRT timestamp."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
