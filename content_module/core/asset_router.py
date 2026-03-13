"""
Asset Router — deterministic routing of assets to the correct generator.

Rules:
  - has_character=True  → z_image generator
  - has_character=False → nano_banana generator
  - reel start frame    → z_image generator
  - reel final render   → kling generator

This is a pure Python module — no LLM calls.
"""

from typing import Literal

GeneratorName = Literal["z_image", "nano_banana", "kling"]


def route_asset(
    has_character: bool,
    content_type: str,
    asset_role: str = "",
) -> GeneratorName:
    """
    Determine which generator to use for an asset.

    Args:
        has_character: Whether the asset contains the main character.
        content_type: 'post', 'story', or 'reel'
        asset_role: Optional role hint ('start_frame', 'motion_reference', 'final_render', etc.)

    Returns:
        Generator name: 'z_image', 'nano_banana', or 'kling'
    """
    # Reel-specific routing
    if content_type == "reel":
        if asset_role == "final_render":
            return "kling"
        if asset_role == "start_frame":
            return "z_image"
        if asset_role == "motion_reference":
            # Motion reference is user-provided, not generated
            # But if somehow needed, it would go through kling
            return "kling"

    # General routing: character → z_image, world → nano_banana
    if has_character:
        return "z_image"
    return "nano_banana"


def route_plan(plan: dict) -> list[dict]:
    """
    Route all assets in a plan to their generators.

    Args:
        plan: Plan dict with 'assets' list.

    Returns:
        List of routing decisions.
    """
    content_type = plan.get("content_type", "post")
    decisions = []

    for asset in plan.get("assets", []):
        generator = route_asset(
            has_character=asset.get("has_character", False),
            content_type=content_type,
            asset_role=asset.get("role", ""),
        )
        decisions.append({
            "index": asset.get("index", len(decisions)),
            "role": asset.get("role", ""),
            "has_character": asset.get("has_character", False),
            "generator": generator,
        })

    return decisions


def suggest_asset_mix(
    total_slides: int,
    content_type: str = "post",
    character_ratio: float = 0.65,
) -> list[dict]:
    """
    Suggest which slides should have character vs world content.

    Args:
        total_slides: Number of slides/frames.
        content_type: 'post' or 'story'
        character_ratio: Target ratio of character slides (0.0-1.0).

    Returns:
        List of suggested asset specs.
    """
    char_count = max(1, round(total_slides * character_ratio))
    world_count = total_slides - char_count

    assets = []
    # Interleave character and world for variety
    char_placed = 0
    world_placed = 0

    for i in range(total_slides):
        # First and last tend to be character
        if i == 0 or (i == total_slides - 1 and char_placed < char_count):
            is_char = True
        elif char_placed >= char_count:
            is_char = False
        elif world_placed >= world_count:
            is_char = True
        else:
            # Alternate, favoring character
            is_char = (i % 3 != 1)  # char, char, world pattern

        if is_char and char_placed < char_count:
            has_character = True
            char_placed += 1
        elif not is_char and world_placed < world_count:
            has_character = False
            world_placed += 1
        elif char_placed < char_count:
            has_character = True
            char_placed += 1
        else:
            has_character = False
            world_placed += 1

        generator = route_asset(has_character, content_type)
        assets.append({
            "index": i,
            "role": f"slide_{i+1}",
            "has_character": has_character,
            "generator": generator,
        })

    return assets
