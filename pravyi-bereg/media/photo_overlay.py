"""Photo overlay: darken photo + add title text on top."""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import GENERATED_DIR, FONTS_DIR, BRAND_NAME

log = logging.getLogger(__name__)

# Target output size for VK posts
TARGET_WIDTH = 1200
TARGET_HEIGHT = 800

# Font settings
TITLE_FONT_SIZE = 64
BRAND_FONT_SIZE = 28
WATERMARK_FONT_SIZE = 20


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if custom not found."""
    # Try custom fonts first
    font_names = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "FreeSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    ]
    
    # Check system font paths
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
    
    # Fallback to default
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
    """Create a post cover image: darkened photo + title text overlay.
    
    Args:
        photo_path: Path to source photo
        title: Title text to overlay
        output_filename: Output filename (auto-generated if None)
        
    Returns:
        Path to generated image
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load and resize photo
    img = Image.open(photo_path).convert("RGB")
    
    # Resize to target dimensions (crop to fill)
    img_ratio = img.width / img.height
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT
    
    if img_ratio > target_ratio:
        # Image is wider - crop sides
        new_height = img.height
        new_width = int(new_height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, new_height))
    else:
        # Image is taller - crop top/bottom
        new_width = img.width
        new_height = int(new_width / target_ratio)
        top = (img.height - new_height) // 2
        img = img.crop((0, top, new_width, top + new_height))
    
    img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
    
    # Apply dark gradient overlay (darker at top for text readability)
    overlay = Image.new("RGBA", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # Gradient: dark at top (for title), lighter in middle, darker at bottom (for brand)
    for y in range(TARGET_HEIGHT):
        if y < TARGET_HEIGHT * 0.4:
            # Top area - darker
            alpha = int(180 - (y / (TARGET_HEIGHT * 0.4)) * 80)
        elif y < TARGET_HEIGHT * 0.7:
            # Middle area - lighter
            alpha = 100
        else:
            # Bottom area - slightly darker
            progress = (y - TARGET_HEIGHT * 0.7) / (TARGET_HEIGHT * 0.3)
            alpha = int(100 + progress * 60)
        
        draw_overlay.rectangle(
            [(0, y), (TARGET_WIDTH, y + 1)],
            fill=(0, 0, 0, alpha),
        )
    
    # Merge overlay
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")
    
    draw = ImageDraw.Draw(img)
    
    # Draw title text
    title_font = _get_font(TITLE_FONT_SIZE, bold=True)
    
    # Word wrap title
    wrapped_lines = _wrap_text(title, title_font, TARGET_WIDTH - 120, draw)
    
    # Calculate vertical position (center in top 40%)
    line_height = TITLE_FONT_SIZE + 10
    total_text_height = len(wrapped_lines) * line_height
    y_start = max(40, (int(TARGET_HEIGHT * 0.35) - total_text_height) // 2)
    
    # Draw each line with shadow
    for i, line in enumerate(wrapped_lines):
        y = y_start + i * line_height
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_width = bbox[2] - bbox[0]
        x = (TARGET_WIDTH - text_width) // 2
        
        # Shadow
        draw.text((x + 2, y + 2), line, font=title_font, fill=(0, 0, 0, 200))
        draw.text((x + 1, y + 1), line, font=title_font, fill=(0, 0, 0, 150))
        # Main text
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255))
    
    # Draw brand name at bottom
    brand_font = _get_font(BRAND_FONT_SIZE, bold=True)
    brand_text = BRAND_NAME
    bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_width = bbox[2] - bbox[0]
    brand_x = (TARGET_WIDTH - brand_width) // 2
    brand_y = TARGET_HEIGHT - 60
    
    # Brand with subtle background
    draw.text((brand_x + 1, brand_y + 1), brand_text, font=brand_font, fill=(0, 0, 0, 180))
    draw.text((brand_x, brand_y), brand_text, font=brand_font, fill=(255, 255, 255, 230))
    
    # Save
    if output_filename is None:
        import time
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
    
    # Limit to 3 lines max
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:40] + "..."
    
    return lines
