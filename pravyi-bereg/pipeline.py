"""Content pipeline: generate → queue → approve → publish."""
from __future__ import annotations

import logging
from datetime import datetime

from db import (
    execute_insert, execute, get_queue_item, get_least_used_photo,
    record_media_usage,
)
from content.generator import generate_post, generate_post_title, generate_reel_script
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
        {'queue_id': int, 'topic': str, 'cover_path': str} or {'error': str}
    """
    # 1. Select topic
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
        # Try to fix by regenerating (don't block - just log warning)
    
    # 3. Generate photo cover
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
    """Pipeline for video reel with TTS."""
    from config import SOURCE_VIDEOS
    
    # Check if we have source videos
    videos = list(SOURCE_VIDEOS.glob("*.mp4")) + list(SOURCE_VIDEOS.glob("*.mov"))
    if not videos:
        log.warning("No source videos available for reel, falling back to post")
        return {"error": "No source videos available. Upload videos via TG bot first."}
    
    # 1. Generate reel script
    script = await generate_reel_script(topic)
    if "error" in script:
        return script
    
    voiceover_text = script["voiceover_text"]
    scenes = script["scenes"]
    
    # 2. Generate TTS
    from tts.voice import generate_tts
    audio_path = await generate_tts(voiceover_text)
    if not audio_path:
        return {"error": "TTS generation failed"}
    
    # 3. Build video
    from media.video_builder import select_video_fragments, build_reel, get_audio_duration
    
    audio_duration = get_audio_duration(audio_path)
    fragments = select_video_fragments(audio_duration, scenes)
    
    if not fragments:
        return {"error": "No video fragments could be selected"}
    
    video_path = build_reel(
        audio_path=audio_path,
        fragments=fragments,
        subtitle_text=voiceover_text,
    )
    
    if not video_path:
        return {"error": "Video assembly failed"}
    
    # 4. Add to queue
    queue_id = execute_insert(
        """INSERT INTO content_queue 
           (post_type, topic, format, hook, text_content, content_hash, 
            media_ids, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
        ("reel", topic, category, topic[:100], voiceover_text, "",
         video_path, datetime.now().isoformat()),
    )
    
    log.info("Reel queued: #%d topic=%s", queue_id, topic)
    
    return {
        "queue_id": queue_id,
        "topic": topic,
        "text": voiceover_text,
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
        # Upload video first, then post with attachment
        if media_path:
            attachment = await upload_video(media_path, item["topic"])
            if attachment:
                vk_post_id = await publish_post(text, photo_path=None)
                # TODO: attach video to post
            else:
                vk_post_id = await publish_post(text)
        else:
            vk_post_id = await publish_post(text)
    else:
        log.error("Unknown post type: %s", post_type)
        return None
    
    if vk_post_id:
        # Update queue
        execute_insert(
            "UPDATE content_queue SET status='published', published_at=? WHERE id=?",
            (datetime.now().isoformat(), queue_id),
        )
        
        # Record in published posts
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
