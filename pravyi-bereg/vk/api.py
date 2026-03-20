"""VK API wrapper for publishing posts and managing community."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import aiohttp

from config import VK_COMMUNITY_TOKEN, VK_USER_TOKEN, VK_COMMUNITY_ID

log = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
VK_API_BASE = "https://api.vk.com/method"


async def _call_api(method: str, params: dict, use_user_token: bool = False) -> dict | None:
    """Call VK API method."""
    token = VK_USER_TOKEN if use_user_token else VK_COMMUNITY_TOKEN
    if not token:
        log.error("VK token not configured (user=%s)", use_user_token)
        return None

    params["access_token"] = token
    params["v"] = VK_API_VERSION

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{VK_API_BASE}/{method}",
                data=params,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    log.error("VK API error in %s: %s", method, data["error"])
                    return None
                return data.get("response")
    except Exception as e:
        log.error("VK API call %s failed: %s", method, e)
        return None


async def upload_photo_to_wall(photo_path: str) -> str | None:
    """Upload a photo to VK wall and return attachment string.
    
    Returns 'photo{owner_id}_{photo_id}' or None.
    """
    # Step 1: Get upload server
    result = await _call_api("photos.getWallUploadServer", {
        "group_id": VK_COMMUNITY_ID,
    }, use_user_token=True)
    
    if not result or "upload_url" not in result:
        log.error("Failed to get wall upload server")
        return None
    
    upload_url = result["upload_url"]
    
    # Step 2: Upload photo
    try:
        async with aiohttp.ClientSession() as session:
            with open(photo_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("photo", f, filename=Path(photo_path).name)
                
                async with session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    upload_result = await resp.json()
    except Exception as e:
        log.error("Photo upload failed: %s", e)
        return None
    
    if not upload_result.get("photo") or upload_result["photo"] == "[]":
        log.error("Photo upload returned empty result")
        return None
    
    # Step 3: Save wall photo
    save_result = await _call_api("photos.saveWallPhoto", {
        "group_id": VK_COMMUNITY_ID,
        "photo": upload_result["photo"],
        "server": upload_result["server"],
        "hash": upload_result["hash"],
    }, use_user_token=True)
    
    if not save_result or not save_result:
        log.error("Failed to save wall photo")
        return None
    
    photo = save_result[0]
    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    log.info("Photo uploaded: %s", attachment)
    return attachment


async def publish_post(
    text: str,
    photo_path: str | None = None,
    video_attachment: str | None = None,
    publish_date: int | None = None,
) -> int | None:
    """Publish a post to VK community wall.
    
    Args:
        text: Post text
        photo_path: Optional path to cover image
        video_attachment: Optional pre-uploaded video attachment string (e.g. 'video-123_456')
        publish_date: Optional Unix timestamp for scheduled post
        
    Returns:
        Post ID or None on error.
    """
    params = {
        "owner_id": -VK_COMMUNITY_ID,
        "from_group": 1,
        "message": text,
    }
    
    attachments = []
    
    # Upload photo if provided
    if photo_path and Path(photo_path).exists():
        photo_att = await upload_photo_to_wall(photo_path)
        if photo_att:
            attachments.append(photo_att)
    
    # Add video attachment if provided
    if video_attachment:
        attachments.append(video_attachment)
    
    if attachments:
        params["attachments"] = ",".join(attachments)
    
    # Schedule if needed
    if publish_date:
        params["publish_date"] = publish_date
    
    result = await _call_api("wall.post", params, use_user_token=True)
    
    if result and "post_id" in result:
        log.info("Post published: ID=%d", result["post_id"])
        return result["post_id"]
    
    log.error("Failed to publish post")
    return None


async def upload_video(video_path: str, title: str, description: str = "") -> str | None:
    """Upload a video to VK and return attachment string.
    
    Returns 'video{owner_id}_{video_id}' or None.
    """
    # Step 1: Get video upload server
    result = await _call_api("video.save", {
        "group_id": VK_COMMUNITY_ID,
        "name": title,
        "description": description,
        "is_private": 0,
        "wallpost": 0,
        "repeat": 0,
    }, use_user_token=True)
    
    if not result or "upload_url" not in result:
        log.error("Failed to get video upload server")
        return None
    
    upload_url = result["upload_url"]
    owner_id = result.get("owner_id")
    video_id = result.get("video_id")
    
    # Step 2: Upload video file
    try:
        async with aiohttp.ClientSession() as session:
            with open(video_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("video_file", f, filename=Path(video_path).name)
                
                async with session.post(
                    upload_url, data=form,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    upload_result = await resp.json()
                    log.info("Video upload result: %s", upload_result)
    except Exception as e:
        log.error("Video upload failed: %s", e)
        return None
    
    if owner_id and video_id:
        attachment = f"video{owner_id}_{video_id}"
        log.info("Video uploaded: %s", attachment)
        return attachment
    
    return None


async def publish_clip(video_path: str, description: str = "") -> dict | None:
    """Publish a video as a VK Clip.
    
    Returns clip info dict or None.
    """
    # VK Clips use a different upload flow
    result = await _call_api("clips.getUploadServer", {
        "group_id": VK_COMMUNITY_ID,
    }, use_user_token=True)
    
    if not result or "upload_url" not in result:
        # Fallback: upload as regular video with clip flag
        log.warning("clips.getUploadServer not available, using video.save")
        attachment = await upload_video(video_path, "Клип", description)
        if attachment:
            return {"attachment": attachment, "type": "video"}
        return None
    
    upload_url = result["upload_url"]
    
    try:
        async with aiohttp.ClientSession() as session:
            with open(video_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("file", f, filename=Path(video_path).name)
                if description:
                    form.add_field("description", description)
                
                async with session.post(
                    upload_url, data=form,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    upload_result = await resp.json()
    except Exception as e:
        log.error("Clip upload failed: %s", e)
        return None
    
    # Save clip
    if "video" in upload_result:
        video_data = upload_result["video"]
        return {
            "attachment": f"video{video_data.get('owner_id')}_{video_data.get('id')}",
            "type": "clip",
        }
    
    return upload_result
