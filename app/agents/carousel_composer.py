"""Carousel Composer — creates carousel preview images from generated photos.

Uses Pillow to compose multiple images into grid layouts, add borders,
and create Instagram-ready carousel previews.

Also integrates Nano Banana (Gemini 3.1 Flash Image) via OpenRouter
for AI-powered carousel slide creation with text overlays.
"""

import base64
import io
import logging
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.agents import llm_client

logger = logging.getLogger(__name__)

# Nano Banana = Gemini 3.1 Flash Image on OpenRouter
MODEL_NANO_BANANA = "google/gemini-3.1-flash-image-preview"

# Instagram carousel aspect ratio: 1080x1350 (4:5 portrait)
CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350


def decode_image(image_base64: str) -> Image.Image:
    """Decode a base64-encoded image to PIL Image."""
    raw = base64.b64decode(image_base64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def encode_image(image: Image.Image, fmt: str = "JPEG", quality: int = 92) -> str:
    """Encode a PIL Image to base64 string."""
    buf = io.BytesIO()
    image.save(buf, format=fmt, quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def image_to_bytes(image: Image.Image, fmt: str = "JPEG", quality: int = 92) -> bytes:
    """Convert a PIL Image to bytes."""
    buf = io.BytesIO()
    image.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


def resize_to_carousel(image: Image.Image) -> Image.Image:
    """Resize and crop an image to Instagram carousel format (1080x1350)."""
    target_ratio = CAROUSEL_WIDTH / CAROUSEL_HEIGHT  # 0.8
    img_ratio = image.width / image.height

    if img_ratio > target_ratio:
        # Image is wider — crop sides
        new_w = int(image.height * target_ratio)
        offset = (image.width - new_w) // 2
        image = image.crop((offset, 0, offset + new_w, image.height))
    elif img_ratio < target_ratio:
        # Image is taller — crop top/bottom
        new_h = int(image.width / target_ratio)
        offset = (image.height - new_h) // 2
        image = image.crop((0, offset, image.width, offset + new_h))

    return image.resize((CAROUSEL_WIDTH, CAROUSEL_HEIGHT), Image.LANCZOS)


def create_carousel_preview(
    images_base64: list[str],
    max_cols: int = 2,
    padding: int = 10,
    bg_color: tuple = (20, 20, 20),
    border_color: tuple = (255, 255, 255),
    border_width: int = 3,
    add_numbers: bool = True,
) -> str:
    """Create a grid preview of carousel images.

    Arranges multiple images in a grid layout for preview purposes.

    Args:
        images_base64: List of base64-encoded images.
        max_cols: Maximum columns in grid.
        padding: Padding between images in pixels.
        bg_color: Background color (RGB tuple).
        border_color: Border color around each image.
        border_width: Border width in pixels.
        add_numbers: Whether to add slide numbers.

    Returns:
        Base64-encoded preview image (JPEG).
    """
    if not images_base64:
        raise ValueError("No images provided for carousel preview")

    # Decode and resize all images
    pil_images = []
    for b64 in images_base64:
        try:
            img = decode_image(b64)
            img = resize_to_carousel(img)
            pil_images.append(img)
        except Exception as e:
            logger.warning(f"Failed to decode image for preview: {e}")

    if not pil_images:
        raise ValueError("Could not decode any images")

    n = len(pil_images)
    cols = min(n, max_cols)
    rows = (n + cols - 1) // cols

    # Calculate cell size (thumbnail for preview)
    cell_w = 540
    cell_h = int(cell_w * CAROUSEL_HEIGHT / CAROUSEL_WIDTH)  # maintain ratio

    # Canvas size
    canvas_w = cols * cell_w + (cols + 1) * padding
    canvas_h = rows * cell_h + (rows + 1) * padding

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    for idx, img in enumerate(pil_images):
        row = idx // cols
        col = idx % cols

        x = padding + col * (cell_w + padding)
        y = padding + row * (cell_h + padding)

        # Resize to cell size
        thumb = img.resize((cell_w, cell_h), Image.LANCZOS)

        # Draw border
        if border_width > 0:
            draw.rectangle(
                [x - border_width, y - border_width,
                 x + cell_w + border_width - 1, y + cell_h + border_width - 1],
                outline=border_color,
                width=border_width,
            )

        canvas.paste(thumb, (x, y))

        # Add slide number
        if add_numbers:
            num_text = str(idx + 1)
            # Draw number badge
            badge_size = 40
            badge_x = x + cell_w - badge_size - 10
            badge_y = y + 10
            draw.ellipse(
                [badge_x, badge_y, badge_x + badge_size, badge_y + badge_size],
                fill=(0, 0, 0, 180),
                outline=(255, 255, 255),
            )
            # Center text in badge
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            except (OSError, IOError):
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), num_text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (badge_x + (badge_size - tw) // 2, badge_y + (badge_size - th) // 2),
                num_text,
                fill=(255, 255, 255),
                font=font,
            )

    return encode_image(canvas)


def create_single_preview(
    image_base64: str,
    caption: str = "",
    hashtags: Optional[list[str]] = None,
) -> str:
    """Create a preview of a single post with caption overlay.

    Args:
        image_base64: Base64-encoded image.
        caption: Post caption text.
        hashtags: List of hashtags.

    Returns:
        Base64-encoded preview image (JPEG).
    """
    img = decode_image(image_base64)
    img = resize_to_carousel(img)

    # Add semi-transparent caption bar at bottom
    if caption:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        bar_height = 120
        draw.rectangle(
            [0, img.height - bar_height, img.width, img.height],
            fill=(0, 0, 0, 160),
        )

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        except (OSError, IOError):
            font = ImageFont.load_default()

        # Truncate caption if too long
        display_caption = caption[:120] + "..." if len(caption) > 120 else caption
        draw.text(
            (20, img.height - bar_height + 15),
            display_caption,
            fill=(255, 255, 255),
            font=font,
        )

        if hashtags:
            tags_text = " ".join(hashtags[:5]) + "..."
            try:
                small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            except (OSError, IOError):
                small_font = ImageFont.load_default()
            draw.text(
                (20, img.height - bar_height + 50),
                tags_text,
                fill=(150, 200, 255),
                font=small_font,
            )

        img = img.convert("RGBA")
        img = Image.alpha_composite(img, overlay)
        img = img.convert("RGB")

    return encode_image(img)


# ---------------------------------------------------------------------------
# Nano Banana (Gemini 3.1 Flash Image) — AI-powered carousel slide creation
# ---------------------------------------------------------------------------

async def create_carousel_slide_ai(
    source_image_base64: str,
    slide_instruction: str,
    style: str = "instagram carousel slide",
) -> Optional[str]:
    """Use Nano Banana (Gemini 3.1 Flash Image) to create a carousel slide.

    Takes a source photo and creates a styled carousel slide with text,
    effects, or transformations applied by the AI vision model.

    Args:
        source_image_base64: Base64-encoded source image.
        slide_instruction: What to create (e.g. "Add motivational quote overlay",
            "Create a collage with this photo", "Apply vintage filter and add text").
        style: Overall style guide.

    Returns:
        Base64-encoded result image, or None if generation failed.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"You are creating an {style}. "
                        f"Task: {slide_instruction}\n\n"
                        "Output ONLY the modified image. "
                        "Make it visually stunning and Instagram-ready. "
                        "Use 1080x1350 portrait aspect ratio (4:5)."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{source_image_base64}",
                    },
                },
            ],
        }
    ]

    try:
        api_key = llm_client._get_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/MadMaxForge/test",
            "X-Title": "Instagram Agent System",
        }
        payload = {
            "model": MODEL_NANO_BANANA,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            logger.info(f"Nano Banana request: {slide_instruction[:80]}...")
            resp = await client.post(
                f"{llm_client.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            if resp.status_code >= 400:
                logger.error(f"Nano Banana API error {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            logger.warning("Nano Banana: no choices in response")
            return None

        msg = choices[0].get("message", {})
        content = msg.get("content")

        # Gemini image models may return inline image data
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            # Extract base64 from data URL
                            b64 = url.split(",", 1)[-1] if "," in url else ""
                            if b64:
                                logger.info(f"Nano Banana: got image ({len(b64)} chars b64)")
                                return b64
                    elif part.get("type") == "text":
                        # Check if text contains base64 image data
                        text = part.get("text", "")
                        if len(text) > 1000 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n" for c in text[:100]):
                            logger.info(f"Nano Banana: got base64 text ({len(text)} chars)")
                            return text.replace("\n", "")

        # If content is a string, it might be a text response (not image)
        if isinstance(content, str):
            logger.info(f"Nano Banana: text response ({len(content)} chars), no image generated")

        return None

    except Exception as e:
        logger.error(f"Nano Banana error: {e}")
        return None


async def create_carousel_from_photos(
    photos_base64: list[str],
    slide_instructions: Optional[list[str]] = None,
    fallback_to_pillow: bool = True,
) -> dict:
    """Create a full carousel from source photos using Nano Banana.

    For each photo, either applies an AI transformation or falls back
    to Pillow-based composition.

    Args:
        photos_base64: List of base64-encoded source photos.
        slide_instructions: Per-slide instructions for Nano Banana.
            If None, uses default carousel styling.
        fallback_to_pillow: If True, use Pillow grid if AI fails.

    Returns:
        dict with 'slides' (list of base64 images), 'preview' (grid base64),
        'method' ('nano_banana' or 'pillow').
    """
    if not slide_instructions:
        slide_instructions = [
            f"Create Instagram carousel slide {i + 1}. "
            "Keep the photo as the main element. "
            "Add subtle aesthetic border and slide number."
            for i in range(len(photos_base64))
        ]

    # Pad instructions to match photos
    while len(slide_instructions) < len(photos_base64):
        slide_instructions.append(slide_instructions[-1] if slide_instructions else "Style this photo for Instagram")

    slides: list[str] = []
    method = "nano_banana"

    for i, (photo_b64, instruction) in enumerate(zip(photos_base64, slide_instructions)):
        logger.info(f"Creating carousel slide {i + 1}/{len(photos_base64)}")
        result = await create_carousel_slide_ai(
            source_image_base64=photo_b64,
            slide_instruction=instruction,
        )
        if result:
            slides.append(result)
        else:
            logger.warning(f"Nano Banana failed for slide {i + 1}, using original")
            slides.append(photo_b64)
            method = "pillow_fallback"

    # Create grid preview
    preview = create_carousel_preview(slides) if len(slides) > 1 else None

    return {
        "slides": slides,
        "preview": preview,
        "method": method,
        "slide_count": len(slides),
    }
