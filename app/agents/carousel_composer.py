"""Carousel Composer — creates carousel preview images from generated photos.

Uses Pillow to compose multiple images into grid layouts, add borders,
and create Instagram-ready carousel previews.
"""

import base64
import io
import logging
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

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
