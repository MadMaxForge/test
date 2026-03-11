"""Research Agent — combines Scout + Director functionality.

Responsibilities:
  1. Scrape Instagram profiles (via instagrapi)
  2. Analyze scraped content (photos, captions, hashtags, engagement)
  3. Describe reference photos as TEXT (for prompt generation)
  4. Create a strategic brief for content creation
"""

import base64
import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.agents import llm_client
from app.agents.brand_guide import get_character_appearance, get_restrictions

logger = logging.getLogger(__name__)


async def scrape_profile(
    username: str,
    max_posts: int = 12,
    session_file: Optional[str] = None,
) -> dict:
    """Scrape an Instagram profile using instagrapi.

    Args:
        username: Instagram username (without @).
        max_posts: Maximum number of recent posts to fetch.
        session_file: Path to instagrapi session JSON file.

    Returns:
        dict with profile info, posts, and media URLs.
    """
    from instagrapi import Client

    cl = Client()

    # Load session if available
    if session_file and os.path.exists(session_file):
        cl.load_settings(session_file)
        try:
            cl.login_by_sessionid(cl.settings.get("authorization_data", {}).get("sessionid", ""))
        except Exception:
            logger.warning("Session file login failed, trying credentials...")
            _login_with_credentials(cl)
    else:
        _login_with_credentials(cl)

    # Get user info
    user_info = cl.user_info_by_username(username)
    user_id = user_info.pk

    # Get recent posts
    medias = cl.user_medias(user_id, amount=max_posts)

    posts = []
    for media in medias:
        post_data: dict = {
            "id": str(media.pk),
            "media_type": media.media_type,  # 1=photo, 2=video, 8=carousel
            "caption": media.caption_text if media.caption_text else "",
            "like_count": media.like_count,
            "comment_count": media.comment_count,
            "timestamp": media.taken_at.isoformat() if media.taken_at else None,
            "thumbnail_url": str(media.thumbnail_url) if media.thumbnail_url else None,
        }

        # Collect image URLs
        if media.media_type == 8 and media.resources:
            # Carousel
            post_data["image_urls"] = [str(r.thumbnail_url) for r in media.resources if r.thumbnail_url]
        elif media.thumbnail_url:
            post_data["image_urls"] = [str(media.thumbnail_url)]
        else:
            post_data["image_urls"] = []

        posts.append(post_data)

    profile = {
        "username": username,
        "full_name": user_info.full_name,
        "biography": user_info.biography,
        "follower_count": user_info.follower_count,
        "following_count": user_info.following_count,
        "media_count": user_info.media_count,
        "is_verified": user_info.is_verified,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }

    return profile


def _login_with_credentials(cl: "Client") -> None:
    """Login to Instagram with env credentials."""
    username = os.getenv("INSTA_USER", "")
    password = os.getenv("INSTA_PASS", "")
    if not username or not password:
        raise ValueError("INSTA_USER and INSTA_PASS must be set for scraping")
    cl.login(username, password)


async def download_image(url: str) -> bytes:
    """Download an image from URL and return bytes."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.content


async def describe_reference_photos(
    image_urls: list[str],
    brand_guide: dict,
    max_photos: int = 6,
) -> list[dict]:
    """Download reference photos and describe them in text using vision model.

    This is the CRITICAL step: photos are NOT sent to ComfyUI.
    Instead, we describe them as text -> text becomes basis for generation prompts.

    Args:
        image_urls: URLs of reference images to analyze.
        brand_guide: Character brand guide.
        max_photos: Max photos to describe.

    Returns:
        List of dicts with 'url', 'description', 'outfit', 'background', 'pose', 'lighting'.
    """
    restrictions = get_restrictions(brand_guide)
    restrictions_text = "; ".join(restrictions)

    descriptions = []
    for url in image_urls[:max_photos]:
        try:
            image_bytes = await download_image(url)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            instruction = (
                "Analyze this Instagram photo and describe it in extreme detail for an AI image generation prompt. "
                "Focus on:\n"
                "1. OUTFIT: Describe clothing in detail (fabric, color, fit, style, brand-vibe)\n"
                "2. BACKGROUND: Room/location details, furniture, decorations, depth of field\n"
                "3. POSE: Body position, hand placement, facial expression, gaze direction\n"
                "4. LIGHTING: Light source, color temperature, shadows, neon/ambient mix\n"
                "5. CAMERA: Angle, framing (headshot/half-body/full), perspective level\n"
                "6. MOOD: Overall atmosphere (cozy, energetic, intimate, playful)\n\n"
                f"Content restrictions: {restrictions_text}\n\n"
                "Output as JSON with keys: outfit, background, pose, lighting, camera, mood, overall_description"
            )

            raw = await llm_client.describe_image(
                image_base64=image_b64,
                instruction=instruction,
            )

            # Try to parse JSON from response
            try:
                # Strip markdown if present
                text = raw.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    lines = [ln for ln in lines if not ln.strip().startswith("```")]
                    text = "\n".join(lines)
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"overall_description": raw}

            parsed["url"] = url
            descriptions.append(parsed)

        except Exception as e:
            logger.warning(f"Failed to describe image {url}: {e}")
            descriptions.append({"url": url, "error": str(e)})

    return descriptions


async def analyze_profile(
    profile_data: dict,
    brand_guide: dict,
) -> dict:
    """Analyze scraped profile data and create a strategic content brief.

    Uses LLM to understand the profile's style, engagement patterns,
    and generate content recommendations.

    Args:
        profile_data: Output from scrape_profile().
        brand_guide: Character brand guide.

    Returns:
        dict with analysis and content recommendations.
    """
    char_appearance = get_character_appearance(brand_guide)
    restrictions = get_restrictions(brand_guide)
    style = brand_guide.get("style", {})

    # Build analysis prompt
    profile_summary = {
        "username": profile_data.get("username"),
        "bio": profile_data.get("biography"),
        "followers": profile_data.get("follower_count"),
        "posts_count": profile_data.get("media_count"),
        "recent_posts": [
            {
                "caption": p.get("caption", "")[:200],
                "likes": p.get("like_count"),
                "comments": p.get("comment_count"),
                "type": p.get("media_type"),
            }
            for p in profile_data.get("posts", [])[:10]
        ],
    }

    messages = [
        {
            "role": "system",
            "content": (
                "You are an Instagram content strategist for AI-generated character profiles. "
                "Analyze the reference profile and create a content strategy brief.\n\n"
                f"Our character: {char_appearance}\n"
                f"Style: {json.dumps(style)}\n"
                f"Restrictions: {'; '.join(restrictions)}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Analyze this Instagram profile and create a content brief:\n\n"
                f"{json.dumps(profile_summary, indent=2)}\n\n"
                "Create a JSON response with:\n"
                "- profile_analysis: What makes this profile successful?\n"
                "- content_themes: Top 5 content themes/ideas we should replicate\n"
                "- posting_strategy: Frequency, timing, engagement tips\n"
                "- prompt_ideas: 5 detailed scene ideas for image generation, "
                "each with outfit, background, pose, lighting, camera descriptions\n"
                "- hashtag_recommendations: 15-20 relevant hashtags\n"
                "- caption_style: How captions should be written"
            ),
        },
    ]

    analysis = await llm_client.chat_completion_json(
        messages=messages,
        temperature=0.6,
        max_tokens=4096,
    )

    analysis["scraped_profile"] = {
        "username": profile_data.get("username"),
        "follower_count": profile_data.get("follower_count"),
        "post_count": profile_data.get("media_count"),
    }

    return analysis


async def create_content_brief(
    reference_descriptions: list[dict],
    profile_analysis: dict,
    brand_guide: dict,
    num_images: int = 4,
) -> dict:
    """Create a final content brief combining photo descriptions and profile analysis.

    This is the output the Creative Agent uses to generate prompts.

    Args:
        reference_descriptions: Output from describe_reference_photos().
        profile_analysis: Output from analyze_profile().
        brand_guide: Character brand guide.
        num_images: How many images to plan for the carousel.

    Returns:
        dict with image_plans (list of scene descriptions), caption plan, hashtags.
    """
    char = brand_guide.get("character", {})
    style = brand_guide.get("style", {})
    prompt_tips = brand_guide.get("prompt_template", {}).get("tips", [])

    # Filter successful descriptions
    valid_refs = [d for d in reference_descriptions if "error" not in d]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a creative director for AI-generated Instagram content. "
                "Based on reference photo descriptions and profile analysis, "
                "create a detailed content plan.\n\n"
                f"Character: {char.get('name')}, {char.get('age')} years old\n"
                f"Trigger word for image generation: {char.get('trigger_word')}\n"
                f"Style: {json.dumps(style)}\n"
                f"Prompt writing tips: {json.dumps(prompt_tips)}\n\n"
                "IMPORTANT: Each image prompt MUST:\n"
                "- Start with 'A {trigger_word},'\n"
                "- Be at least 500 characters long\n"
                "- Include: scene + background + outfit (detailed) + pose + camera + lighting\n"
                "- Be in English\n"
                "- NOT reference any real brands or copyrighted characters by name"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Reference photo descriptions:\n{json.dumps(valid_refs, indent=2)}\n\n"
                f"Profile analysis:\n{json.dumps(profile_analysis, indent=2)}\n\n"
                f"Create a JSON content brief with:\n"
                f"- image_plans: array of {num_images} objects, each with:\n"
                f"  - full_prompt: Complete detailed prompt (500+ chars) ready for z-image\n"
                f"  - theme: Short theme name\n"
                f"  - outfit_summary: 1-line outfit description\n"
                f"- caption: Instagram caption text (short, flirty, with emoji)\n"
                f"- hashtags: array of 15-20 relevant hashtags\n"
                f"- carousel_order: recommended order of images in carousel"
            ),
        },
    ]

    brief = await llm_client.chat_completion_json(
        messages=messages,
        temperature=0.7,
        max_tokens=6000,
    )

    return brief
