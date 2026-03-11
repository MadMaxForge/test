"""Publish Agent — assembles carousel, generates captions, prepares post package.

Responsibilities:
  1. Take generated images from Creative Agent
  2. Order images for carousel
  3. Generate/refine caption and hashtags
  4. Create a ready-to-post package for approval
  5. (Future) Post to Instagram via instagrapi
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.agents import llm_client
from app.agents.llm_client import MODEL_SIMPLE
from app.agents.brand_guide import get_posting_rules, get_restrictions

logger = logging.getLogger(__name__)


async def assemble_post(
    carousel_data: dict,
    brand_guide: dict,
) -> dict:
    """Assemble a complete Instagram post package.

    Takes Creative Agent output and prepares it for human approval.

    Args:
        carousel_data: Output from creative_agent.generate_carousel().
        brand_guide: Character brand guide.

    Returns:
        Post package dict ready for Telegram approval.
    """
    images = carousel_data.get("images", [])
    carousel_order = carousel_data.get("carousel_order", list(range(len(images))))

    # Order images
    ordered_images = []
    for idx in carousel_order:
        if idx < len(images):
            ordered_images.append(images[idx])

    # If carousel_order is malformed, use original order
    if not ordered_images:
        ordered_images = images

    # Get or generate caption
    caption = carousel_data.get("caption", "")
    hashtags = carousel_data.get("hashtags", [])

    if not caption or len(caption) < 10:
        caption = await generate_caption(
            images=ordered_images,
            brand_guide=brand_guide,
        )

    if not hashtags:
        hashtags = await generate_hashtags(
            images=ordered_images,
            caption=caption,
            brand_guide=brand_guide,
        )

    # Format final caption
    full_caption = _format_caption(caption, hashtags)

    # Build post package
    post_package = {
        "type": "carousel" if len(ordered_images) > 1 else "single",
        "images": [
            {
                "index": img.get("index", i),
                "theme": img.get("theme", ""),
                "prompt": img.get("prompt", ""),
                "qc_score": img.get("qc_score", 0),
                "image_url": img.get("image_url"),
                "image_base64": img.get("image_base64"),
            }
            for i, img in enumerate(ordered_images)
        ],
        "caption": caption,
        "hashtags": hashtags,
        "full_caption": full_caption,
        "image_count": len(ordered_images),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_approval",
        "avg_qc_score": _average_qc(ordered_images),
    }

    return post_package


async def generate_caption(
    images: list[dict],
    brand_guide: dict,
) -> str:
    """Generate an Instagram caption based on image themes and brand guide.

    Args:
        images: List of generated image dicts.
        brand_guide: Character brand guide.

    Returns:
        Caption text (without hashtags).
    """
    posting = get_posting_rules(brand_guide)
    char = brand_guide.get("character", {})
    style = brand_guide.get("style", {})

    themes = [img.get("theme", "") for img in images if img.get("theme")]

    messages = [
        {
            "role": "system",
            "content": (
                "You are an Instagram caption writer for an AI character profile.\n\n"
                f"Character: {char.get('name')}, {char.get('age')} years old\n"
                f"Tone: {style.get('tone', 'playful, confident')}\n"
                f"Caption style: {posting.get('caption_style', 'short, flirty, with emoji')}\n"
                f"Language: {posting.get('caption_language', 'english')}\n\n"
                "Write SHORT captions (1-3 sentences). Use emoji naturally. "
                "Be flirty and engaging. DO NOT include hashtags in the caption."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Image themes: {', '.join(themes)}\n\n"
                "Write a caption. Output ONLY the caption text, nothing else."
            ),
        },
    ]

    caption = await llm_client.chat_completion(
        messages=messages,
        model=MODEL_SIMPLE,
        temperature=0.9,
        max_tokens=500,
    )

    return caption.strip().strip('"')


async def generate_hashtags(
    images: list[dict],
    caption: str,
    brand_guide: dict,
    count: int = 5,
) -> list[str]:
    """Generate relevant Instagram hashtags.

    Args:
        images: Generated image data.
        caption: The caption text.
        brand_guide: Character brand guide.
        count: Number of hashtags to generate (max 5).

    Returns:
        List of hashtags (with # prefix), max 5.
    """
    # Hard cap at 5 hashtags
    count = min(count, 5)

    style = brand_guide.get("style", {})
    posting = get_posting_rules(brand_guide)
    themes = [img.get("theme", "") for img in images if img.get("theme")]

    messages = [
        {
            "role": "system",
            "content": (
                "You are an Instagram hashtag strategist. Generate EXACTLY 5 hashtags:\n"
                "- 2 popular hashtags (100K+ posts)\n"
                "- 2 medium hashtags (10K-100K posts)\n"
                "- 1 niche hashtag (1K-10K posts)\n\n"
                "IMPORTANT: Output EXACTLY 5 hashtags, no more, no less.\n\n"
                f"Account style: {style.get('aesthetic', 'e-girl / alt')}\n"
                f"Content types: {json.dumps(style.get('content_types', []))}\n"
                f"Hashtag style: {posting.get('hashtag_style', 'mix of popular and niche')}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Themes: {', '.join(themes)}\n"
                f"Caption: {caption}\n\n"
                f"Generate EXACTLY {count} hashtags. Output as JSON array of strings (with # prefix)."
            ),
        },
    ]

    raw = await llm_client.chat_completion(
        messages=messages,
        model=MODEL_SIMPLE,
        temperature=0.6,
        max_tokens=1000,
    )

    # Parse array
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        hashtags = json.loads(text)
        if isinstance(hashtags, list):
            return [h if h.startswith("#") else f"#{h}" for h in hashtags[:count]]
    except json.JSONDecodeError:
        pass

    # Fallback: extract hashtags from text
    words = text.replace(",", " ").replace('"', "").replace("[", "").replace("]", "").split()
    return [w if w.startswith("#") else f"#{w}" for w in words if len(w) > 2][:count]


def _format_caption(caption: str, hashtags: list[str]) -> str:
    """Format caption with hashtags separated by line breaks."""
    tags_text = " ".join(hashtags)
    return f"{caption}\n\n.\n.\n.\n{tags_text}"


def _average_qc(images: list[dict]) -> float:
    """Calculate average QC score across images."""
    scores = [img.get("qc_score", 0) for img in images if img.get("qc_score")]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)
