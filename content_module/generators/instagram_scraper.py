"""
Instagram Scraper — downloads reference content from Instagram URLs.

Uses instagrapi for authenticated access.
Downloads images/videos and creates a manifest JSON for the analyst.

This is a pure Python module — no LLM calls.
"""

import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from content_module.core.config import DOWNLOADS_DIR, JOBS_DIR


def extract_shortcode(url: str) -> Optional[str]:
    """Extract Instagram shortcode from a URL."""
    patterns = [
        r"instagram\.com/p/([A-Za-z0-9_-]+)",
        r"instagram\.com/reel/([A-Za-z0-9_-]+)",
        r"instagram\.com/reels/([A-Za-z0-9_-]+)",
        r"instagram\.com/tv/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_reel_url(url: str) -> bool:
    """Check if URL is a reel."""
    return "/reel/" in url or "/reels/" in url


def download_reference(
    url: str,
    job_id: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Download reference content from an Instagram URL.

    Uses instagrapi if session_id is available, otherwise falls back
    to basic HTTP download of public content.

    Args:
        url: Instagram URL
        job_id: Job ID for organizing downloaded files
        session_id: Optional Instagram session ID for authenticated access

    Returns:
        Manifest dict with downloaded file paths and metadata
    """
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"Could not extract shortcode from URL: {url}")

    ref_dir = JOBS_DIR / job_id / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "url": url,
        "shortcode": shortcode,
        "is_reel": is_reel_url(url),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "metadata": {},
        "method": "none",
    }

    # Try instagrapi first
    if session_id:
        try:
            manifest = _download_with_instagrapi(url, shortcode, ref_dir, session_id, manifest)
            manifest["method"] = "instagrapi"
        except Exception as e:
            print(f"[Scraper] instagrapi failed: {e}, trying fallback...")
            manifest = _download_fallback(url, shortcode, ref_dir, manifest)
            manifest["method"] = "fallback"
    else:
        manifest = _download_fallback(url, shortcode, ref_dir, manifest)
        manifest["method"] = "fallback"

    # Save manifest
    manifest_path = ref_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"[Scraper] Downloaded {len(manifest['files'])} files for {shortcode}")
    return manifest


def _download_with_instagrapi(
    url: str,
    shortcode: str,
    ref_dir: Path,
    session_id: str,
    manifest: dict,
) -> dict:
    """Download using instagrapi library."""
    from instagrapi import Client

    cl = Client()
    cl.delay_range = [2, 5]
    cl.login_by_sessionid(session_id)

    # Get media info by shortcode
    media_pk = cl.media_pk_from_code(shortcode)
    media_info = cl.media_info(media_pk)

    manifest["metadata"] = {
        "media_type": media_info.media_type,
        "caption": (media_info.caption_text or "")[:500],
        "likes": media_info.like_count or 0,
        "comments": media_info.comment_count or 0,
        "taken_at": media_info.taken_at.isoformat() if media_info.taken_at else None,
    }

    if media_info.media_type == 8 and media_info.resources:
        # Carousel
        manifest["metadata"]["carousel_count"] = len(media_info.resources)
        for i, resource in enumerate(media_info.resources[:10]):
            thumb_url = str(resource.thumbnail_url) if resource.thumbnail_url else None
            if thumb_url:
                img_bytes = _download_url(thumb_url)
                if img_bytes:
                    filename = f"{shortcode}_slide_{i+1}.jpg"
                    filepath = ref_dir / filename
                    filepath.write_bytes(img_bytes)
                    manifest["files"].append({
                        "index": i,
                        "type": "image",
                        "path": str(filepath),
                        "size_bytes": len(img_bytes),
                    })

    elif media_info.media_type == 2:
        # Video/Reel
        video_url = str(media_info.video_url) if media_info.video_url else None
        thumb_url = str(media_info.thumbnail_url) if media_info.thumbnail_url else None

        if video_url:
            video_bytes = _download_url(video_url)
            if video_bytes:
                filename = f"{shortcode}_video.mp4"
                filepath = ref_dir / filename
                filepath.write_bytes(video_bytes)
                manifest["files"].append({
                    "index": 0,
                    "type": "video",
                    "path": str(filepath),
                    "size_bytes": len(video_bytes),
                })

        if thumb_url:
            img_bytes = _download_url(thumb_url)
            if img_bytes:
                filename = f"{shortcode}_thumb.jpg"
                filepath = ref_dir / filename
                filepath.write_bytes(img_bytes)
                manifest["files"].append({
                    "index": 1,
                    "type": "thumbnail",
                    "path": str(filepath),
                    "size_bytes": len(img_bytes),
                })

    elif media_info.media_type == 1:
        # Single photo
        thumb_url = str(media_info.thumbnail_url) if media_info.thumbnail_url else None
        if thumb_url:
            img_bytes = _download_url(thumb_url)
            if img_bytes:
                filename = f"{shortcode}_photo.jpg"
                filepath = ref_dir / filename
                filepath.write_bytes(img_bytes)
                manifest["files"].append({
                    "index": 0,
                    "type": "image",
                    "path": str(filepath),
                    "size_bytes": len(img_bytes),
                })

    return manifest


def _download_fallback(
    url: str,
    shortcode: str,
    ref_dir: Path,
    manifest: dict,
) -> dict:
    """
    Fallback download without authentication.
    Tries to get at least the page metadata.
    This is limited — many posts require auth.
    """
    print(f"[Scraper] Fallback mode for {shortcode} (limited without session)")
    manifest["metadata"]["note"] = "Downloaded without authentication — content may be incomplete"
    return manifest


def _download_url(url: str, timeout: int = 120) -> Optional[bytes]:
    """Download content from a URL."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            return resp.content
        print(f"[Scraper] Download failed ({resp.status_code}): {url[:80]}")
        return None
    except Exception as e:
        print(f"[Scraper] Download error: {e}")
        return None
