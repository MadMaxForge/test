"""Photo overlay: darkened photo + emerald-themed title overlay for VK posts."""
from __future__ import annotations

import logging
import time
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import (
    GENERATED_DIR, FONTS_DIR, BRAND_NAME,
    BRAND_COLOR_PRIMARY, BRAND_COLOR_DARK, BRAND_COLOR_ACCENT,
)

log = logging.getLogger(__name__)

# Target output size for VK posts
TARGET_WIDTH = 1200
TARGET_HEIGHT = 800

# Font settings
TITLE_FONT_SIZE = 60
BRAND_FONT_SIZE = 26
SUBTITLE_FONT_SIZE = 22


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if custom not found."""
    font_names = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "FreeSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    ]
    font_dirs = [
        FONTS_DIR,
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/freefont"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/TTF"),
    ]
    for font_dir in font_dirs:
        for font_name in font_names:
            font_path = font_dir / font_name
            if font_path.exists():
                try:
                    return ImageFont.truetype(str(font_path), size)
                except Exception:
                    continue
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        log.warning("No TrueType font found, using default bitmap font")
        return ImageFont.load_default()


def create_post_cover(
    photo_path: str,
    title: str,
    output_filename: str | None = None,
) -> str:
    """Create a post cover image with emerald-green brand theme.

    - Darkened photo background with emerald tint
    - Decorative top/bottom bars in brand color
    - Title text centered with glow effect
    - Brand name watermark at bottom
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Load and resize photo
    img = Image.open(photo_path).convert("RGB")

    # Crop to fill target dimensions
    img_ratio = img.width / img.height
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT

    if img_ratio > target_ratio:
        new_height = img.height
        new_width = int(new_height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, new_height))
    else:
        new_width = img.width
        new_height = int(new_width / target_ratio)
        top = (img.height - new_height) // 2
        img = img.crop((0, top, new_width, top + new_height))

    img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)

    # Apply dark overlay with emerald tint
    overlay = Image.new("RGBA", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # Gradient: emerald-tinted darkening
    er, eg, eb = BRAND_COLOR_DARK  # Dark emerald
    for y in range(TARGET_HEIGHT):
        if y < 6:
            # Top accent bar
            alpha = 255
            r, g, b = BRAND_COLOR_PRIMARY
        elif y < TARGET_HEIGHT * 0.15:
            # Top area - very dark with emerald
            progress = y / (TARGET_HEIGHT * 0.15)
            alpha = int(200 - progress * 40)
            r, g, b = er, eg, eb
        elif y < TARGET_HEIGHT * 0.65:
            # Middle area - medium dark for text readability
            alpha = 150
            r, g, b = er // 2, eg // 2, eb // 2
        elif y < TARGET_HEIGHT * 0.85:
            # Lower area - darker again
            progress = (y - TARGET_HEIGHT * 0.65) / (TARGET_HEIGHT * 0.2)
            alpha = int(150 + progress * 50)
            r, g, b = er // 2, eg // 2, eb // 2
        elif y >= TARGET_HEIGHT - 6:
            # Bottom accent bar
            alpha = 255
            r, g, b = BRAND_COLOR_PRIMARY
        else:
            alpha = 190
            r, g, b = er // 2, eg // 2, eb // 2

        draw_overlay.rectangle(
            [(0, y), (TARGET_WIDTH, y + 1)],
            fill=(r, g, b, alpha),
        )

    # Merge overlay
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")

    draw = ImageDraw.Draw(img)

    # Draw decorative line under top bar
    accent_r, accent_g, accent_b = BRAND_COLOR_ACCENT
    draw.rectangle([(40, 70), (TARGET_WIDTH - 40, 72)], fill=(accent_r, accent_g, accent_b))

    # Draw title text
    title_font = _get_font(TITLE_FONT_SIZE, bold=True)
    wrapped_lines = _wrap_text(title, title_font, TARGET_WIDTH - 160, draw)

    line_height = TITLE_FONT_SIZE + 14
    total_text_height = len(wrapped_lines) * line_height
    y_start = max(100, (int(TARGET_HEIGHT * 0.45) - total_text_height) // 2 + 40)

    for i, line in enumerate(wrapped_lines):
        y = y_start + i * line_height
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_width = bbox[2] - bbox[0]
        x = (TARGET_WIDTH - text_width) // 2

        # Emerald glow effect (multiple shadow layers)
        for offset in (3, 2, 1):
            glow_alpha = 80 + offset * 30
            draw.text((x + offset, y + offset), line, font=title_font,
                       fill=(er, eg, eb))
        # Main white text
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255))

    # Draw decorative line above brand
    brand_y_line = TARGET_HEIGHT - 80
    draw.rectangle([(40, brand_y_line), (TARGET_WIDTH - 40, brand_y_line + 2)],
                   fill=(accent_r, accent_g, accent_b))

    # Draw brand name at bottom
    brand_font = _get_font(BRAND_FONT_SIZE, bold=True)
    bbox = draw.textbbox((0, 0), BRAND_NAME, font=brand_font)
    brand_width = bbox[2] - bbox[0]
    brand_x = (TARGET_WIDTH - brand_width) // 2
    brand_y = TARGET_HEIGHT - 55

    draw.text((brand_x + 1, brand_y + 1), BRAND_NAME, font=brand_font,
              fill=(er, eg, eb))
    draw.text((brand_x, brand_y), BRAND_NAME, font=brand_font,
              fill=(accent_r, accent_g, accent_b))

    # Save
    if output_filename is None:
        output_filename = f"post_cover_{int(time.time())}.jpg"

    output_path = GENERATED_DIR / output_filename
    img.save(str(output_path), "JPEG", quality=92)
    log.info("Post cover created: %s", output_path)

    return str(output_path)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:40] + "..."

    return lines
