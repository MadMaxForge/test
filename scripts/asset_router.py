#!/usr/bin/env python3
"""
Asset Router - Deterministic routing logic for generator selection.

Routes assets to the correct generator based on simple if/else rules:
- has_character=True + type=reel_start_frame → z_image (frame) then kling (motion)
- has_character=True → z_image
- has_character=False → nano_banana

NO LLM calls. Pure deterministic logic.

Usage:
    from asset_router import AssetRouter
    router = AssetRouter()
    generator = router.route(has_character=True, asset_type="post_slide")
    # Returns: "z_image"
"""

# Generator configurations
GENERATOR_CONFIG = {
    "z_image": {
        "name": "Z-Image (RunPod)",
        "supports_character": True,
        "api": "runpod",
        "timeout_sec": 600,
        "prompt_prefix": "A w1man, ",
        "max_prompt_length": 2000,
        "resolutions": {
            "feed": "1080x1350",
            "story": "1088x1920",
            "reel": "1088x1920",
        },
    },
    "nano_banana": {
        "name": "Nano Banana (Evolink)",
        "supports_character": False,
        "api": "evolink",
        "timeout_sec": 180,
        "prompt_prefix": "",
        "max_prompt_length": 150,
        "prompt_rules": [
            "NO person or character references",
            "100-150 characters max",
            "Must include 'real photo' or 'photorealistic'",
        ],
        "resolutions": {
            "feed": "1080x1350",
            "story": "1088x1920",
            "reel": "1088x1920",
        },
    },
    "kling": {
        "name": "Kling Motion Control (Evolink)",
        "supports_character": True,
        "api": "evolink",
        "timeout_sec": 600,
        "requires_start_frame": True,
        "requires_motion_ref": True,
        "note": "Two-stage: approve start_frame BEFORE launching Kling render",
        "resolutions": {
            "reel": "1088x1920",
        },
    },
}

# Content format specifications
FORMAT_SPECS = {
    "feed": {
        "aspect_ratio": "4:5",
        "resolution": "1080x1350",
        "orientation": "portrait",
    },
    "story": {
        "aspect_ratio": "9:16",
        "resolution": "1088x1920",
        "orientation": "vertical",
    },
    "reel": {
        "aspect_ratio": "9:16",
        "resolution": "1088x1920",
        "orientation": "vertical",
    },
}

# Asset type to content format mapping
TYPE_TO_FORMAT = {
    "post_slide": "feed",
    "story_frame": "story",
    "reel_start_frame": "reel",
}


class AssetRouter:
    """Deterministic router for asset → generator mapping."""

    def route(self, has_character, asset_type):
        """
        Route an asset to the correct generator.

        Args:
            has_character: bool - whether the asset contains a character
            asset_type: str - one of: post_slide, story_frame, reel_start_frame

        Returns:
            str - generator name: "z_image", "nano_banana", or "z_image" (for reel start frame)
        """
        if asset_type == "reel_start_frame":
            # Reel start frames always go through z_image first
            # Kling is used later for motion, not for the start frame
            return "z_image"

        if has_character:
            return "z_image"
        else:
            return "nano_banana"

    def get_reel_motion_generator(self):
        """Get the generator for reel motion (always Kling)."""
        return "kling"

    def get_generator_config(self, generator_name):
        """Get configuration for a specific generator."""
        if generator_name not in GENERATOR_CONFIG:
            raise ValueError("Unknown generator: %s. Valid: %s" % (
                generator_name, ", ".join(GENERATOR_CONFIG.keys())))
        return GENERATOR_CONFIG[generator_name]

    def get_format_spec(self, asset_type):
        """Get the content format specification for an asset type."""
        content_format = TYPE_TO_FORMAT.get(asset_type)
        if not content_format:
            raise ValueError("Unknown asset type: %s" % asset_type)
        return FORMAT_SPECS[content_format]

    def get_content_format(self, asset_type):
        """Get content format string for an asset type."""
        return TYPE_TO_FORMAT.get(asset_type, "feed")

    def get_resolution(self, generator_name, asset_type):
        """Get the resolution for a generator + asset type combination."""
        config = self.get_generator_config(generator_name)
        content_format = self.get_content_format(asset_type)
        return config["resolutions"].get(content_format, "1080x1350")

    def get_timeout(self, generator_name):
        """Get the timeout in seconds for a generator."""
        config = self.get_generator_config(generator_name)
        return config["timeout_sec"]

    def validate_plan(self, planned_assets):
        """
        Validate that a list of planned assets has correct routing.

        Args:
            planned_assets: list of dicts with keys:
                asset_id, type, has_character, generator

        Returns:
            tuple (is_valid: bool, errors: list[str], warnings: list[str])
        """
        errors = []
        warnings = []

        character_count = 0
        world_count = 0

        for asset in planned_assets:
            asset_id = asset.get("asset_id", "unknown")
            asset_type = asset.get("type")
            has_char = asset.get("has_character", False)
            generator = asset.get("generator")

            # Check routing correctness
            expected = self.route(has_char, asset_type)
            if generator != expected:
                errors.append(
                    "%s: generator should be '%s' (got '%s') for "
                    "has_character=%s, type=%s" % (
                        asset_id, expected, generator, has_char, asset_type))

            # Count character vs world
            if has_char:
                character_count += 1
            else:
                world_count += 1

        # Check character/world ratio (60-70% character target)
        total = character_count + world_count
        if total > 0:
            ratio = character_count / total
            if ratio < 0.5:
                warnings.append(
                    "Character ratio %.0f%% is below 50%%. Target: 60-70%%." % (
                        ratio * 100))
            elif ratio > 0.8:
                warnings.append(
                    "Character ratio %.0f%% is above 80%%. Target: 60-70%%." % (
                        ratio * 100))

        is_valid = len(errors) == 0
        return is_valid, errors, warnings

    def suggest_mix(self, post_count, story_count, has_reel):
        """
        Suggest a character/world mix for a given package composition.

        Args:
            post_count: int - number of post slides
            story_count: int - number of story frames
            has_reel: bool - whether there's a reel

        Returns:
            dict with suggested character/world split per type
        """
        total = post_count + story_count + (1 if has_reel else 0)
        target_character = round(total * 0.65)  # 65% character target
        target_world = total - target_character

        suggestion = {
            "total": total,
            "target_character": target_character,
            "target_world": target_world,
            "target_ratio": round(target_character / total, 2) if total > 0 else 0,
            "posts": {"character": 0, "world": 0},
            "stories": {"character": 0, "world": 0},
            "reel": {"character": 1 if has_reel else 0},
        }

        # Reel is always character
        remaining_character = target_character - (1 if has_reel else 0)

        # Distribute posts: first slide always character, last can be world
        if post_count > 0:
            post_char = min(remaining_character, max(1, post_count - 1))
            post_world = post_count - post_char
            suggestion["posts"]["character"] = post_char
            suggestion["posts"]["world"] = post_world
            remaining_character -= post_char

        # Distribute stories: at least 1 character if any remain
        if story_count > 0:
            story_char = min(remaining_character, max(1, story_count - 1))
            story_world = story_count - story_char
            suggestion["stories"]["character"] = story_char
            suggestion["stories"]["world"] = story_world

        return suggestion


# ── CLI for testing ──────────────────────────────────────────────

def main():
    import sys
    import json

    router = AssetRouter()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 asset_router.py route <has_character:true/false> <asset_type>")
        print("  python3 asset_router.py validate <plan.json>")
        print("  python3 asset_router.py suggest <posts> <stories> <has_reel:true/false>")
        print("  python3 asset_router.py config <generator_name>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "route":
        has_char = sys.argv[2].lower() == "true"
        asset_type = sys.argv[3]
        generator = router.route(has_char, asset_type)
        resolution = router.get_resolution(generator, asset_type)
        print("Generator: %s" % generator)
        print("Resolution: %s" % resolution)
        print("Timeout: %ds" % router.get_timeout(generator))

    elif cmd == "validate":
        with open(sys.argv[2]) as f:
            plan = json.load(f)
        assets = plan.get("assets", plan) if isinstance(plan, dict) else plan
        is_valid, errors, warnings = router.validate_plan(assets)
        print("Valid: %s" % is_valid)
        for e in errors:
            print("  ERROR: %s" % e)
        for w in warnings:
            print("  WARNING: %s" % w)

    elif cmd == "suggest":
        posts = int(sys.argv[2])
        stories = int(sys.argv[3])
        has_reel = sys.argv[4].lower() == "true" if len(sys.argv) > 4 else False
        suggestion = router.suggest_mix(posts, stories, has_reel)
        print(json.dumps(suggestion, indent=2))

    elif cmd == "config":
        config = router.get_generator_config(sys.argv[2])
        print(json.dumps(config, indent=2, default=str))

    else:
        print("Unknown command: %s" % cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
