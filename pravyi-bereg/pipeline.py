"""Content pipeline: generate -> queue -> approve -> publish."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from db import (
    execute_insert, execute, get_queue_item, get_least_used_photo,
    record_media_usage,
)
from content.generator import generate_post, generate_post_title, generate_reel_script, generate_reel_caption
from content.topics import get_next_topic
from content.validator import validate_post
from media.photo_overlay import create_post_cover

log = logging.getLogger(__name__)


async def generate_and_queue_post(
    post_type: str = "post",
    force_topic: str | None = None,
) -> dict | None:
    """Generate content and add to approval queue.

    Args:
        post_type: 'post' or 'reel'
        force_topic: Override automatic topic selection

    Returns:
        {'queue_id': int, 'topic': str, ...} or {'error': str}
    """
    try:
        if force_topic:
            topic = force_topic
            category = "manual"
        else:
            topic_info = get_next_topic(post_type)
            topic = topic_info["topic"]
            category = topic_info["category"]

        log.info("Generating %s on topic: %s", post_type, topic)

        if post_type == "post":
            return await _generate_post_pipeline(topic, category)
        elif post_type == "reel":
            return await _generate_reel_pipeline(topic, category)
        else:
            return {"error": f"Unknown post type: {post_type}"}
    except Exception as e:
        log.error("Pipeline error for %s: %s", post_type, e, exc_info=True)
        return {"error": f"Pipeline {post_type}: {e}"}


async def _generate_post_pipeline(topic: str, category: str) -> dict:
    """Pipeline for text post with photo cover."""
    # 1. Generate post text
    result = await generate_post(topic)
    if "error" in result:
        return result

    text = result["text"]
    content_hash = result["content_hash"]
    hook = result["hook"]

    # 2. Validate
    validation = validate_post(text)
    if not validation["passed"]:
        log.warning("Post validation issues: %s", validation["issues"])

    # 3. Generate photo cover with emerald theme
    photo_path = get_least_used_photo()
    cover_path = None

    if photo_path:
        title = await generate_post_title(topic)
        try:
            cover_path = create_post_cover(photo_path, title)
            log.info("Cover created: %s", cover_path)
        except Exception as e:
            log.error("Cover creation failed: %s", e)

    # 4. Add to queue
    queue_id = execute_insert(
        """INSERT INTO content_queue
           (post_type, topic, format, hook, text_content, content_hash,
            media_ids, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
        ("post", topic, category, hook, text, content_hash,
         cover_path or "", datetime.now().isoformat()),
    )

    # 5. Record media usage
    if photo_path:
        record_media_usage(photo_path, queue_id)

    log.info("Post queued: #%d topic=%s", queue_id, topic)

    return {
        "queue_id": queue_id,
        "topic": topic,
        "text": text,
        "cover_path": cover_path,
        "post_type": "post",
    }


async def _generate_reel_pipeline(topic: str, category: str) -> dict:
    """Full pipeline for video reel: script -> TTS -> lip-sync -> composite video."""
    from config import SOURCE_VIDEOS, AVATAR_IMAGE_PATH

    # Check prerequisites
    videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))
    if not videos:
        return {"error": "No source videos available. Upload videos via TG bot first."}

    # 1. Generate reel script (AI)
    log.info("[Reel] Step 1/5: Generating script...")
    script = await generate_reel_script(topic)
    if "error" in script:
        return script

    voiceover_text = script["voiceover_text"]
    log.info("[Reel] Script: %d words", len(voiceover_text.split()))

    # 2. Generate TTS with word-level timestamps
    log.info("[Reel] Step 2/5: Generating TTS with timestamps...")
    from tts.voice import generate_tts_with_timestamps

    try:
        audio_path, word_timestamps = await generate_tts_with_timestamps(voiceover_text)
    except Exception as e:
        return {"error": f"ElevenLabs TTS failed: {e}"}
    if not audio_path:
        return {"error": "TTS generation failed (ElevenLabs)"}

    log.info("[Reel] TTS: %s, %d word timestamps", audio_path, len(word_timestamps))

    # 3. Generate lip-sync avatar video via RunPod
    log.info("[Reel] Step 3/5: Generating lip-sync avatar (RunPod)...")
    from media.runpod_lipsync import generate_lipsync_video

    try:
        lipsync_path = await asyncio.to_thread(
            generate_lipsync_video, audio_path, AVATAR_IMAGE_PATH or None
        )
    except Exception as e:
        return {"error": f"RunPod lip-sync failed: {e}"}
    if not lipsync_path:
        return {"error": "RunPod lip-sync failed: no output video"}

    log.info("[Reel] Lip-sync video: %s", lipsync_path)

    # 4. Build composite reel (BG video + avatar + subtitles + audio)
    log.info("[Reel] Step 4/5: Building composite video...")
    from media.video_builder import build_composite_reel

    # Use a random source video for ambient background audio (10% volume)
    import random as _rng
    bg_audio_source = str(_rng.choice(videos)) if videos else None

    video_path = build_composite_reel(
        tts_audio_path=audio_path,
        lipsync_video_path=lipsync_path,
        word_timestamps=word_timestamps,
        bg_audio_source=bg_audio_source,
        bg_music_volume=0.1,
    )

    if not video_path:
        return {"error": "Video assembly failed"}

    # 5. Generate short CTA caption (not the voiceover text)
    log.info("[Reel] Step 5/6: Generating caption...")
    caption = await generate_reel_caption(topic)
    log.info("[Reel] Caption: %s", caption)

    # 6. Add to queue
    log.info("[Reel] Step 6/6: Adding to approval queue...")
    queue_id = execute_insert(
        """INSERT INTO content_queue
           (post_type, topic, format, hook, text_content, content_hash,
            media_ids, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
        ("reel", topic, category, topic[:100], caption, "",
         video_path, datetime.now().isoformat()),
    )

    log.info("[Reel] Reel queued: #%d topic=%s video=%s", queue_id, topic, video_path)

    return {
        "queue_id": queue_id,
        "topic": topic,
        "text": caption,
        "video_path": video_path,
        "post_type": "reel",
    }


async def publish_approved_item(queue_id: int) -> int | None:
    """Publish an approved queue item to VK.

    Returns VK post ID or None.
    """
    from vk.api import publish_post, upload_video

    item = get_queue_item(queue_id)
    if not item:
        log.error("Queue item #%d not found", queue_id)
        return None

    if item["status"] != "approved":
        log.error("Queue item #%d status is %s, not approved", queue_id, item["status"])
        return None

    post_type = item["post_type"]
    text = item["text_content"]
    media_path = item["media_ids"]  # cover_path for posts, video_path for reels

    if post_type == "post":
        vk_post_id = await publish_post(text, photo_path=media_path if media_path else None)
    elif post_type == "reel":
        if media_path:
            attachment = await upload_video(media_path, item["topic"])
            if attachment:
                vk_post_id = await publish_post(text, video_attachment=attachment)
            else:
                vk_post_id = await publish_post(text)
        else:
            vk_post_id = await publish_post(text)
    else:
        log.error("Unknown post type: %s", post_type)
        return None

    if vk_post_id:
        execute_insert(
            "UPDATE content_queue SET status='published', published_at=? WHERE id=?",
            (datetime.now().isoformat(), queue_id),
        )
        execute_insert(
            """INSERT INTO published_posts
               (vk_post_id, post_type, topic, format, text_content, content_hash,
                media_ids, published_at, photo_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vk_post_id, post_type, item["topic"], item["format"],
             text, item["content_hash"], media_path,
             datetime.now().isoformat(), media_path),
        )
        log.info("Published #%d to VK: post_id=%d", queue_id, vk_post_id)

    return vk_post_id
