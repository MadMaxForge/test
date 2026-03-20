"""Video builder: assemble reels with lip-sync avatar (50/50 layout) + ASS subtitles."""
from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from pathlib import Path

from config import GENERATED_DIR, SOURCE_VIDEOS, REEL_AUDIO_BUFFER

log = logging.getLogger(__name__)

# Video output settings
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920  # Vertical (9:16) for reels
VIDEO_FPS = 30
VIDEO_BITRATE = "4M"
HALF_HEIGHT = VIDEO_HEIGHT // 2  # 960px for each half

# Subtitle settings
SUBTITLE_FONT = "DejaVu Sans"
SUBTITLE_FONTSIZE = 40
WORDS_PER_SUBTITLE = 5


def get_media_duration(path: str) -> float:
    """Get media duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        log.error("Failed to get duration for %s: %s", path, e)
        return 0.0


def generate_ass_subtitles(
    word_timestamps: list[dict],
    output_path: str,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str:
    """Generate ASS subtitle file from word-level timestamps."""
    if not word_timestamps:
        log.warning("No word timestamps provided for subtitles")
        return output_path

    blocks = []
    current_block: list[dict] = []

    for w in word_timestamps:
        current_block.append(w)
        word = w["word"]
        is_sentence_end = word.rstrip().endswith((".", "!", "?", ":"))
        if len(current_block) >= WORDS_PER_SUBTITLE or is_sentence_end:
            blocks.append(current_block)
            current_block = []

    if current_block:
        blocks.append(current_block)

    sub_y = play_res_y // 2 - 20

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{SUBTITLE_FONT},{SUBTITLE_FONTSIZE},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,5,20,20,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for block in blocks:
        start_time = block[0]["start"]
        end_time = block[-1]["end"]
        text = " ".join(w["word"] for w in block)

        words_list = text.split()
        if len(words_list) > 3:
            mid = len(words_list) // 2
            line1 = " ".join(words_list[:mid])
            line2 = " ".join(words_list[mid:])
            text = line1 + r"\N" + line2

        start_str = _format_ass_time(start_time)
        end_str = _format_ass_time(end_time)

        pos_tag = r"{\pos(" + str(play_res_x // 2) + "," + str(sub_y) + ")}"
        ass_lines.append(
            f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{pos_tag}{text}"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines) + "\n")

    log.info("ASS subtitles generated: %s (%d blocks)", output_path, len(blocks))
    return output_path


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp (H:MM:SS.cc)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _get_least_used_video() -> Path | None:
    """Pick the least-recently-used source video for background rotation."""
    from db import execute
    source_videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))
    if not source_videos:
        return None

    # Build usage map {file_path_str: count}
    usage: dict[str, int] = {}
    for row in execute("SELECT file_path, COUNT(*) as cnt FROM media_usage WHERE file_path LIKE '%.mp4' OR file_path LIKE '%.mov' GROUP BY file_path"):
        usage[row["file_path"]] = row["cnt"]

    # Sort: least used first, then shuffle among ties for variety
    source_videos.sort(key=lambda p: (usage.get(str(p), 0), random.random()))
    return source_videos[0]


def select_background_video(duration: float) -> str | None:
    """Select a background video from source videos (least-used first) and prepare it."""
    video_file = _get_least_used_video()
    if not video_file:
        log.error("No source videos found in %s", SOURCE_VIDEOS)
        return None

    # Record usage so this video won't be picked next time
    from db import record_media_usage
    record_media_usage(str(video_file), 0)

    log.info("Selected background video: %s", video_file)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    bg_path = str(GENERATED_DIR / f"bg_norm_{int(time.time())}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(video_file),
        "-vf", f"scale={VIDEO_WIDTH}:{HALF_HEIGHT}:force_original_aspect_ratio=increase,"
               f"crop={VIDEO_WIDTH}:{HALF_HEIGHT},setsar=1,fps={VIDEO_FPS}",
        "-c:v", "libx264", "-preset", "fast",
        "-an", "-t", str(duration),
        bg_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        log.error("BG video normalization failed: %s", result.stderr[:500])
        return None

    log.info("Background video prepared: %s (%.1fs)", bg_path, duration)
    return bg_path


def prepare_avatar_video(lipsync_path: str, duration: float) -> str | None:
    """Prepare lip-sync avatar video for bottom half. No black side bars."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    avatar_path = str(GENERATED_DIR / f"avatar_norm_{int(time.time())}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-i", lipsync_path,
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_WIDTH},crop={VIDEO_WIDTH}:{HALF_HEIGHT}:0:60,"
               f"setsar=1,fps={VIDEO_FPS}",
        "-c:v", "libx264", "-preset", "fast",
        "-an", "-t", str(duration),
        avatar_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("Avatar video preparation failed: %s", result.stderr[:500])
        return None

    log.info("Avatar video prepared: %s", avatar_path)
    return avatar_path


def build_composite_reel(
    tts_audio_path: str,
    lipsync_video_path: str,
    word_timestamps: list[dict],
    bg_audio_source: str | None = None,
    bg_music_volume: float = 0.1,
    output_filename: str | None = None,
) -> str | None:
    """Build the final composite reel: BG video (top 50%) + avatar (bottom 50%) + subtitles + audio."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if output_filename is None:
        output_filename = f"reel_{int(time.time())}.mp4"
    output_path = str(GENERATED_DIR / output_filename)

    tts_duration = get_media_duration(tts_audio_path)
    if tts_duration <= 0:
        log.error("Invalid TTS audio duration")
        return None

    if word_timestamps:
        last_word_end = max(w["end"] for w in word_timestamps)
        target_duration = last_word_end + REEL_AUDIO_BUFFER
    else:
        last_word_end = tts_duration
        target_duration = tts_duration + REEL_AUDIO_BUFFER

    log.info("Target reel duration: %.2fs (last word: %.2fs + %.1fs buffer)",
             target_duration, last_word_end, REEL_AUDIO_BUFFER)

    try:
        bg_video_path = select_background_video(target_duration)
        if not bg_video_path:
            return None

        avatar_video_path = prepare_avatar_video(lipsync_video_path, target_duration)
        if not avatar_video_path:
            return None

        stacked_path = str(GENERATED_DIR / f"stacked_{int(time.time())}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", bg_video_path, "-i", avatar_video_path,
            "-filter_complex", "[0:v][1:v]vstack=inputs=2[vout]",
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "fast", "-b:v", VIDEO_BITRATE,
            "-t", str(target_duration),
            stacked_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            log.error("Stacking failed: %s", result.stderr[:500])
            return None

        ass_path = str(GENERATED_DIR / f"subs_{int(time.time())}.ass")
        generate_ass_subtitles(word_timestamps, ass_path)

        subs_path = str(GENERATED_DIR / f"stacked_subs_{int(time.time())}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", stacked_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-b:v", VIDEO_BITRATE,
            subs_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            log.error("Subtitle burn failed: %s", result.stderr[:500])
            return None

        audio_inputs = ["-i", subs_path, "-i", tts_audio_path]
        filter_parts = []

        if bg_audio_source and Path(bg_audio_source).exists():
            bg_audio_loop = str(GENERATED_DIR / f"bg_audio_{int(time.time())}.m4a")
            cmd_bg = [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-i", bg_audio_source,
                "-vn", "-c:a", "aac", "-t", str(target_duration + 1),
                bg_audio_loop,
            ]
            subprocess.run(cmd_bg, capture_output=True, text=True, timeout=60)

            if Path(bg_audio_loop).exists():
                audio_inputs.extend(["-i", bg_audio_loop])
                filter_parts.append(
                    f"[1:a]volume=1.0[tts];"
                    f"[2:a]volume={bg_music_volume}[bg];"
                    f"[tts][bg]amix=inputs=2:duration=longest[aout]"
                )
            else:
                filter_parts.append("[1:a]volume=1.0[aout]")
        else:
            filter_parts.append("[1:a]volume=1.0[aout]")

        cmd = ["ffmpeg", "-y"] + audio_inputs
        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-b:v", VIDEO_BITRATE,
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(target_duration),
            "-movflags", "+faststart",
            output_path,
        ])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            log.error("Final audio mix failed: %s", result.stderr[:500])
            return None

        final_duration = get_media_duration(output_path)
        log.info("Composite reel built: %s (%.1fs)", output_path, final_duration)
        return output_path

    finally:
        for pattern in ("bg_norm_*", "avatar_norm_*", "stacked_*", "bg_audio_*"):
            for f in GENERATED_DIR.glob(pattern):
                try:
                    f.unlink()
                except Exception:
                    pass


# Legacy aliases
def get_audio_duration(audio_path: str) -> float:
    return get_media_duration(audio_path)

def get_video_duration(video_path: str) -> float:
    return get_media_duration(video_path)
