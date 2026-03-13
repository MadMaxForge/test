#!/usr/bin/env python3
"""
Package Planner — decides asset composition for a content package.

Takes Scout analysis output and decides:
- Number of post slides, stories, reel start frame
- Character vs world per asset
- Generator routing (via AssetRouter)
- Brief for each asset (hint to Creative, not a prompt)

Planner CANNOT write prompts, generate, or modify Scout analysis.

Usage:
    from package_planner import PackagePlanner
    planner = PackagePlanner()
    plan = planner.create_plan(package_id, theme, scout_analysis, has_reel=True)
"""

import json
import os
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


class PackagePlanner:
    """Creates asset plans from Scout analysis."""

    def __init__(self):
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from asset_router import AssetRouter
        self.router = AssetRouter()

    def create_plan(self, package_id, theme, scout_analysis,
                    has_reel=False, reel_motion_ref=None):
        """
        Create a package plan from scout analysis.

        Args:
            package_id: str
            theme: str
            scout_analysis: dict - Scout agent output
            has_reel: bool - whether to include reel
            reel_motion_ref: str | None - path to motion reference video

        Returns:
            dict - the package plan
        """
        slides = scout_analysis.get("individual_analyses",
                                     scout_analysis.get("slides", []))
        slide_count = len(slides)

        # Determine composition based on reference
        post_count = self._decide_post_count(slide_count)
        story_count = self._decide_story_count(post_count)

        # Get character/world mix
        mix = self.router.suggest_mix(post_count, story_count, has_reel)

        plan = {
            "plan_id": "plan_%s" % package_id,
            "package_id": package_id,
            "theme": theme,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "based_on_reference": scout_analysis.get("reference_id", "unknown"),
            "assets": [],
            "summary": {},
        }

        # Build post slide assets
        char_remaining = mix["posts"]["character"]
        for i in range(1, post_count + 1):
            has_char = char_remaining > 0
            if has_char:
                char_remaining -= 1

            generator = self.router.route(has_char, "post_slide")
            role = self._decide_role(has_char, i, post_count, "post_slide")
            brief = self._build_brief(slides, i - 1, theme, "post_slide")

            plan["assets"].append({
                "asset_id": "post_slide_%d" % i,
                "type": "post_slide",
                "order": i,
                "role": role,
                "has_character": has_char,
                "generator": generator,
                "brief": brief,
                "mirrors_reference_slide": i if i <= slide_count else None,
            })

        # Build story frame assets
        story_char_remaining = mix["stories"]["character"]
        for i in range(1, story_count + 1):
            has_char = story_char_remaining > 0
            if has_char:
                story_char_remaining -= 1

            generator = self.router.route(has_char, "story_frame")
            role = self._decide_role(has_char, i, story_count, "story_frame")
            brief = self._build_brief(slides, post_count + i - 1, theme, "story_frame")

            plan["assets"].append({
                "asset_id": "story_frame_%d" % i,
                "type": "story_frame",
                "order": i,
                "role": role,
                "has_character": has_char,
                "generator": generator,
                "brief": brief,
                "mirrors_reference_slide": None,
            })

        # Build reel start frame if needed
        if has_reel:
            plan["assets"].append({
                "asset_id": "reel_1",
                "type": "reel_start_frame",
                "order": 1,
                "role": "motion_content",
                "has_character": True,
                "generator": "z_image",
                "brief": "Reel start frame: %s, clean background for motion transfer" % theme,
                "motion_ref": reel_motion_ref,
                "mirrors_reference_slide": None,
            })

        # Build summary
        total = len(plan["assets"])
        char_count = sum(1 for a in plan["assets"] if a["has_character"])
        world_count = total - char_count

        plan["summary"] = {
            "total_assets": total,
            "post_slides": post_count,
            "story_frames": story_count,
            "reel_start_frames": 1 if has_reel else 0,
            "character_assets": char_count,
            "world_assets": world_count,
            "character_ratio": round(char_count / total, 2) if total > 0 else 0,
        }

        # Validate
        is_valid, errors, warnings = self.router.validate_plan(plan["assets"])
        if errors:
            print("[Planner] Validation errors: %s" % errors)
        for w in warnings:
            print("[Planner] Warning: %s" % w)

        plan["validation"] = {
            "is_valid": is_valid,
            "errors": errors,
            "warnings": warnings,
        }

        print("[Planner] Plan: %d posts + %d stories + %d reel = %d total (%.0f%% character)" % (
            post_count, story_count, 1 if has_reel else 0, total,
            plan["summary"]["character_ratio"] * 100))

        return plan

    def _decide_post_count(self, reference_slide_count):
        """Decide how many post slides based on reference."""
        if reference_slide_count == 0:
            return 3  # Default
        return min(reference_slide_count, 4)  # Cap at 4

    def _decide_story_count(self, post_count):
        """Decide story count as supplement to posts."""
        if post_count <= 2:
            return 2
        if post_count <= 3:
            return 2
        return 1  # 4 posts + 1 story

    def _decide_role(self, has_character, position, total, asset_type):
        """Decide the role for an asset based on position and type."""
        if asset_type == "reel_start_frame":
            return "motion_content"

        if asset_type == "story_frame":
            if has_character:
                return "behind_the_scenes"
            return "lifestyle_detail"

        # Post slides
        if has_character:
            if position == 1:
                return "hero_character"
            return "character_scene"
        else:
            if position == total:
                return "closing_detail"
            return "world_detail"

    def _build_brief(self, slides, index, theme, asset_type):
        """Build a brief description for Creative agent."""
        if index < len(slides) and isinstance(slides[index], dict):
            slide = slides[index]
            parts = []
            if slide.get("background"):
                parts.append(str(slide["background"])[:60])
            if slide.get("mood"):
                parts.append("mood: %s" % str(slide["mood"])[:30])
            if slide.get("clothing"):
                parts.append("outfit: %s" % str(slide["clothing"])[:40])
            if parts:
                return "; ".join(parts)

        # Fallback brief
        if asset_type == "story_frame":
            return "Story supplement for %s theme" % theme
        return "%s themed content" % theme


# ── CLI ──────────────────────────────────────────────────────────

def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 package_planner.py <scout_analysis.json> [--reel]")
        print("  python3 package_planner.py demo")
        sys.exit(0)

    if sys.argv[1] == "demo":
        planner = PackagePlanner()
        mock_scout = {
            "reference_id": "ref_demo",
            "individual_analyses": [
                {"background": "pool area", "mood": "relaxed", "clothing": "white bikini"},
                {"background": "tropical garden", "mood": "serene", "clothing": "floral dress"},
                {"background": "sunset ocean", "mood": "dreamy", "clothing": "none visible"},
            ],
        }
        plan = planner.create_plan(
            "pkg_demo", "poolside luxury", mock_scout, has_reel=True)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        with open(sys.argv[1]) as f:
            scout = json.load(f)
        has_reel = "--reel" in sys.argv
        planner = PackagePlanner()
        plan = planner.create_plan("pkg_test", "test", scout, has_reel=has_reel)
        print(json.dumps(plan, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
