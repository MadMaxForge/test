#!/usr/bin/env python3
"""
Instagram Scraper using instagrapi library.
Downloads profile data + post images and creates a manifest JSON for Scout agent.

Usage:
  python3 instagram_scraper.py <username> [--count N] [--session-id SID]
  python3 instagram_scraper.py --test  # test session validity
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Base paths
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
DOWNLOADS_DIR = os.path.join(WORKSPACE, "downloads")
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
SESSION_DIR = os.path.join(WORKSPACE, "sessions")
ANALYSIS_DIR = os.path.join(WORKSPACE, "scout_analysis")


def get_session_id():
    """Get Instagram session ID from env or file."""
    sid = os.environ.get("INSTA_SESSION_ID", "")
    if not sid:
        cookie_file = os.path.join(SESSION_DIR, "session_cookie.txt")
        if os.path.exists(cookie_file):
            sid = open(cookie_file).read().strip()
    return sid


def create_client(session_id=None):
    """Create and authenticate instagrapi client."""
    from instagrapi import Client

    cl = Client()
    cl.delay_range = [3, 7]  # random delay between requests (anti-ban)

    if session_id:
        # Login via session_id cookie
        cl.login_by_sessionid(session_id)
        print(f"[OK] Logged in as: {cl.account_info().username}")
    else:
        print("[ERROR] No session_id provided. Set INSTA_SESSION_ID env or save to session_cookie.txt")
        sys.exit(1)

    return cl


def test_session(session_id):
    """Test if session is valid."""
    try:
        cl = create_client(session_id)
        info = cl.account_info()
        print(f"[OK] Session valid!")
        print(f"  Username: {info.username}")
        print(f"  Full name: {info.full_name}")
        print(f"  Verified: {info.is_verified}")
        return True
    except Exception as e:
        print(f"[FAIL] Session invalid: {e}")
        return False


def scrape_profile(username, session_id, post_count=12):
    """
    Scrape Instagram profile: download posts + images, create manifest.
    Returns path to manifest.json
    """
    cl = create_client(session_id)

    # Get user info
    print(f"[*] Fetching profile: @{username}")
    user_id = cl.user_id_from_username(username)
    user_info = cl.user_info(user_id)

    profile = {
        "username": user_info.username,
        "full_name": user_info.full_name,
        "biography": user_info.biography or "",
        "followers": user_info.follower_count,
        "following": user_info.following_count,
        "post_count": user_info.media_count,
        "is_verified": user_info.is_verified,
        "is_private": user_info.is_private,
        "profile_pic_url": str(user_info.profile_pic_url_hd or user_info.profile_pic_url or ""),
        "external_url": str(user_info.external_url or ""),
        "category": user_info.category or "",
    }
    print(f"  {profile['full_name']} | {profile['followers']} followers | {profile['post_count']} posts")

    if user_info.is_private:
        print("[WARN] Profile is private - can only scrape if following")

    # Create download directory
    dl_dir = os.path.join(DOWNLOADS_DIR, username)
    os.makedirs(dl_dir, exist_ok=True)

    # Download profile picture
    print(f"[*] Downloading profile picture...")
    try:
        pp_path = cl.photo_download_by_url(
            str(user_info.profile_pic_url_hd or user_info.profile_pic_url),
            folder=dl_dir,
            filename="profile_pic"
        )
        profile["local_profile_pic"] = str(pp_path)
    except Exception as e:
        print(f"  [WARN] Could not download profile pic: {e}")
        profile["local_profile_pic"] = ""

    # Get posts
    print(f"[*] Fetching {post_count} posts...")
    medias = cl.user_medias(user_id, amount=post_count)
    print(f"  Got {len(medias)} posts")

    # Load memory to check already processed
    memory_file = os.path.join(MEMORY_DIR, "scout_memory.json")
    os.makedirs(MEMORY_DIR, exist_ok=True)
    memory = {}
    if os.path.exists(memory_file):
        with open(memory_file) as f:
            memory = json.load(f)

    processed_ids = set(
        memory.get("profiles", {}).get(username, {}).get("processed_post_ids", [])
    )

    posts = []
    for i, media in enumerate(medias):
        shortcode = media.code or str(media.pk)

        # Determine media type
        if media.media_type == 1:
            post_type = "photo"
        elif media.media_type == 2:
            post_type = "video" if not getattr(media, 'product_type', None) == 'clips' else "reel"
        elif media.media_type == 8:
            post_type = "carousel"
        else:
            post_type = "unknown"

        # Download image(s)
        local_files = []
        try:
            if media.media_type == 8 and media.resources:
                # Carousel - download first image (or all)
                for j, resource in enumerate(media.resources[:5]):  # max 5 per carousel
                    if resource.thumbnail_url:
                        fname = f"{shortcode}_slide{j+1}"
                        img_path = cl.photo_download_by_url(
                            str(resource.thumbnail_url),
                            folder=dl_dir,
                            filename=fname
                        )
                        local_files.append(str(img_path))
            elif media.media_type == 1 and media.thumbnail_url:
                # Single photo
                img_path = cl.photo_download_by_url(
                    str(media.thumbnail_url),
                    folder=dl_dir,
                    filename=shortcode
                )
                local_files.append(str(img_path))
            elif media.media_type == 2 and media.thumbnail_url:
                # Video thumbnail
                img_path = cl.photo_download_by_url(
                    str(media.thumbnail_url),
                    folder=dl_dir,
                    filename=f"{shortcode}_thumb"
                )
                local_files.append(str(img_path))
        except Exception as e:
            print(f"  [WARN] Could not download media for {shortcode}: {e}")

        caption_text = media.caption_text or ""
        timestamp = int(media.taken_at.timestamp()) if media.taken_at else 0

        post_data = {
            "shortcode": shortcode,
            "post_type": post_type,
            "likes": media.like_count or 0,
            "comments": media.comment_count or 0,
            "caption": caption_text[:500],  # truncate long captions
            "timestamp": timestamp,
            "local_files": local_files,
            "is_new": shortcode not in processed_ids,
        }

        if media.media_type == 8 and media.resources:
            post_data["carousel_count"] = len(media.resources)

        posts.append(post_data)
        print(f"  [{i+1}/{len(medias)}] {shortcode} ({post_type}) - {len(local_files)} files")

    # Create manifest
    manifest = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "instagrapi",
        "profile": profile,
        "posts": posts,
        "total_posts_scraped": len(posts),
        "new_posts": sum(1 for p in posts if p.get("is_new")),
    }

    manifest_path = os.path.join(dl_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Manifest saved: {manifest_path}")
    print(f"  Total: {len(posts)} posts ({manifest['new_posts']} new)")

    # Update memory
    all_ids = list(processed_ids | {p["shortcode"] for p in posts})
    memory.setdefault("profiles", {})
    memory["profiles"][username] = {
        "last_scraped": manifest["scraped_at"],
        "first_scraped": memory.get("profiles", {}).get(username, {}).get(
            "first_scraped", manifest["scraped_at"]
        ),
        "processed_post_ids": all_ids,
        "total_posts_analyzed": len(all_ids),
    }
    with open(memory_file, "w") as f:
        json.dump(memory, f, indent=2)
    print(f"[OK] Memory updated: {memory_file}")

    return manifest_path


def main():
    parser = argparse.ArgumentParser(description="Instagram Scraper (instagrapi)")
    parser.add_argument("username", nargs="?", help="Instagram username to scrape")
    parser.add_argument("--count", type=int, default=12, help="Number of posts to fetch (default: 12)")
    parser.add_argument("--session-id", help="Instagram session ID (or set INSTA_SESSION_ID env)")
    parser.add_argument("--test", action="store_true", help="Test session validity")

    args = parser.parse_args()

    session_id = args.session_id or get_session_id()

    if args.test:
        ok = test_session(session_id)
        sys.exit(0 if ok else 1)

    if not args.username:
        parser.error("username is required (or use --test)")

    scrape_profile(args.username, session_id, args.count)


if __name__ == "__main__":
    main()
