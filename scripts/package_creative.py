#!/usr/bin/env python3
"""
Package Creative Agent — generates prompts for all assets in a package plan.

Takes a package plan (from PackagePlanner) and generates detailed image prompts
for each asset using the Creative LLM (kimi-k2.5).

Key differences from legacy creative_agent.py:
- Works per-package, not per-username
- Generates prompts for posts + stories + reel start frames
- Reads plan.assets[] and produces one prompt per asset
- Enforces generator-specific rules (Z-Image prefix, Nano Banana length)
- Outputs prompt_hash for version tracking

Usage:
    from package_creative import PackageCreative
    creative = PackageCreative()
    result = creative.generate_prompts(package_id, plan, scout_analysis)

CLI:
    python3 package_creative.py <plan.json> [<scout_analysis.json>]
"""

import json
import os
import re
import sys
import hashlib
import requests
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

CREATIVE_SYSTEM_PROMPT = """You are Creative Agent for an Instagram AI content pipeline.

You receive a PACKAGE PLAN with assets and write detailed generation prompts for each.

=== GENERATORS ===

1. Z-Image (w1man LoRA) — CHARACTER photos with FACE visible
   - Prompt MUST start with "A w1man, "
   - Follow the block template: setting → background → appearance → outfit → camera
   - 150-400 words
   - Resolution: feed=1080x1350, story/reel=1088x1920

2. Nano Banana — NON-CHARACTER: landscapes, objects, details, architecture
   - NO "A w1man" — NO person in these shots
   - 100-150 characters only (short!)
   - MUST include "real photo" or "photorealistic"
   - Resolution: feed=1080x1350, story=1088x1920

=== Z-IMAGE TEMPLATE ===

A w1man, {SETTING}

Background:
{BACKGROUND with depth of field, blur/bokeh}

{APPEARANCE: hair, expression, makeup, emotion}

Outfit ({OUTFIT_NAME}):
{Every garment: type, color, material, fit}

{CAMERA: position, framing, focal length, lighting}

=== NANO BANANA EXAMPLES ===
- "Wooden cabin by frozen lake, pine trees, fresh snow, golden hour, real photo, photorealistic"
- "Bowl of cherries on sunlit terrace, overhead view, real photo, no illustration"

=== RULES ===
- Each prompt UNIQUE — different pose, angle, moment
- ALL prompts share ONE theme (same location/season/mood)
- Outfits CAN change within the theme
- AVOID mirrors, glass, reflective surfaces
- Reel start frames: clean background for motion transfer
- Stories: can be more casual, behind-the-scenes feel

=== OUTPUT FORMAT ===

Output ONLY valid JSON:
{
  "prompts": [
    {
      "asset_id": "post_slide_1",
      "type": "post_slide",
      "generator": "z_image",
      "content_format": "feed",
      "resolution": "1080x1350",
      "prompt": "full prompt text",
      "mood": "mood keyword",
      "camera_framing": "close-up / medium / full body"
    }
  ]
}

IMPORTANT: One prompt per asset. asset_id must match the plan exactly."""


def parse_json_response(text):
    """Robustly parse JSON from LLM response."""
    if text is None:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        candidate = m.group(1)
        open_b = candidate.count("{") - candidate.count("}")
        open_a = candidate.count("[") - candidate.count("]")
        if open_b > 0 or open_a > 0:
            last_complete = max(
                candidate.rfind('",'),
                candidate.rfind('"],'),
                candidate.rfind('},'))
            if last_complete > 0:
                candidate = candidate[:last_complete + 1]
            candidate += "]" * max(0, open_a) + "}" * max(0, open_b)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    print("[PackageCreative] Could not parse JSON from response:\n%s" % text[:500])
    return None


# Resolution map
RESOLUTIONS = {
    "post_slide": {"format": "feed", "resolution": "1080x1350"},
    "story_frame": {"format": "story", "resolution": "1088x1920"},
    "reel_start_frame": {"format": "reel", "resolution": "1088x1920"},
}


class PackageCreative:
    """Generates prompts for all assets in a package plan."""

    def __init__(self, model=None, api_key=None):
        self.model = model or MODEL
        self.api_key = api_key or OPENROUTER_API_KEY

    def generate_prompts(self, package_id, plan, scout_analysis=None):
        """
        Generate prompts for all assets in the plan.

        Args:
            package_id: str
            plan: dict - from PackagePlanner
            scout_analysis: dict | None - from Scout agent

        Returns:
            dict with "prompts" array, one per asset
        """
        theme = plan.get("theme", "lifestyle")
        assets = plan.get("assets", [])

        if not assets:
            print("[PackageCreative] No assets in plan")
            return {"prompts": []}

        # Build LLM prompt
        user_prompt = self._build_user_prompt(theme, assets, scout_analysis)

        print("[PackageCreative] Calling %s for %d assets..." % (
            self.model, len(assets)))

        result = self._call_llm(user_prompt)

        if not result or "prompts" not in result:
            print("[PackageCreative] LLM failed, building fallback prompts")
            result = self._build_fallback(theme, assets)

        # Post-process: enforce rules
        result = self._post_process(result, assets)

        # Add metadata
        result["creative_id"] = "creative_%s" % package_id
        result["package_id"] = package_id
        result["theme"] = theme
        result["created_at"] = datetime.now(timezone.utc).isoformat()
        result["model"] = self.model

        print("[PackageCreative] Generated %d prompts for theme '%s'" % (
            len(result.get("prompts", [])), theme))

        return result

    def _build_user_prompt(self, theme, assets, scout_analysis):
        """Build the user prompt for Creative LLM."""
        lines = ["Package theme: %s\n" % theme]
        lines.append("Assets to generate prompts for:\n")

        for asset in assets:
            res_info = RESOLUTIONS.get(asset["type"], RESOLUTIONS["post_slide"])
            lines.append("- %s: type=%s, generator=%s, role=%s, character=%s, format=%s, resolution=%s" % (
                asset["asset_id"], asset["type"], asset["generator"],
                asset.get("role", "?"), asset["has_character"],
                res_info["format"], res_info["resolution"]))
            if asset.get("brief"):
                lines.append("  Brief: %s" % asset["brief"])

        # Add scout context if available
        if scout_analysis:
            slides = scout_analysis.get("individual_analyses",
                                         scout_analysis.get("slides", []))
            if slides:
                lines.append("\nReference analysis (for inspiration):")
                for i, slide in enumerate(slides[:4]):
                    if isinstance(slide, dict):
                        lines.append("  Ref %d: bg=%s, mood=%s, clothing=%s" % (
                            i + 1,
                            str(slide.get("background", "?"))[:80],
                            str(slide.get("mood", "?"))[:40],
                            str(slide.get("clothing", "?"))[:60]))

            agg = scout_analysis.get("aggregate_style", {})
            if agg:
                lines.append("\nOverall style: %s" % str(agg.get(
                    "mood_palette", agg.get("clothing_style", "?")))[:100])

        lines.append("\nGenerate exactly ONE prompt per asset. asset_id must match exactly.")
        lines.append("All prompts share the theme '%s'. Each prompt unique." % theme)
        lines.append("AVOID mirrors, glass, reflective surfaces.")
        lines.append("Output ONLY the JSON.")

        return "\n".join(lines)

    def _call_llm(self, user_prompt):
        """Call OpenRouter API."""
        if not self.api_key:
            print("[PackageCreative] No API key")
            return None

        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer %s" % self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": CREATIVE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 8000,
                },
                timeout=120,
            )

            if resp.status_code != 200:
                print("[PackageCreative] API error %d: %s" % (
                    resp.status_code, resp.text[:300]))
                return None

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return None

            return parse_json_response(content)

        except Exception as e:
            print("[PackageCreative] Request error: %s" % str(e))
            return None

    def _post_process(self, result, plan_assets):
        """Enforce generator-specific rules on all prompts."""
        # Build lookup for plan assets
        plan_lookup = {a["asset_id"]: a for a in plan_assets}

        for prompt_item in result.get("prompts", []):
            asset_id = prompt_item.get("asset_id", "")
            plan_asset = plan_lookup.get(asset_id, {})
            generator = plan_asset.get("generator", prompt_item.get("generator", "z_image"))
            asset_type = plan_asset.get("type", prompt_item.get("type", "post_slide"))

            # Ensure correct generator from plan
            prompt_item["generator"] = generator
            prompt_item["type"] = asset_type

            # Set resolution info
            res_info = RESOLUTIONS.get(asset_type, RESOLUTIONS["post_slide"])
            prompt_item["content_format"] = res_info["format"]
            prompt_item["resolution"] = res_info["resolution"]

            prompt_text = prompt_item.get("prompt", "")

            if generator == "z_image":
                # Ensure starts with "A w1man, "
                if not prompt_text.startswith("A w1man"):
                    prompt_item["prompt"] = "A w1man, " + prompt_text

            elif generator == "nano_banana":
                # Remove "A w1man" if present
                if prompt_text.startswith("A w1man, "):
                    prompt_text = prompt_text[len("A w1man, "):]
                elif prompt_text.startswith("A w1man,"):
                    prompt_text = prompt_text[len("A w1man,"):].lstrip()

                # Ensure "real photo" is in prompt
                if "real photo" not in prompt_text.lower() and "photorealistic" not in prompt_text.lower():
                    prompt_text = prompt_text.rstrip(". ") + ", real photo, photorealistic"

                # Enforce 150 char limit
                if len(prompt_text) > 150:
                    prompt_text = prompt_text[:147] + "..."

                prompt_item["prompt"] = prompt_text

            # Add prompt hash
            prompt_item["prompt_hash"] = hashlib.md5(
                prompt_item["prompt"].encode()).hexdigest()[:8]

        return result

    def _build_fallback(self, theme, assets):
        """Build fallback prompts if LLM fails."""
        prompts = []
        for asset in assets:
            generator = asset["generator"]
            asset_type = asset["type"]
            res_info = RESOLUTIONS.get(asset_type, RESOLUTIONS["post_slide"])

            if generator == "z_image":
                prompt = (
                    "A w1man, in a beautiful %s setting, natural pose\n\n"
                    "Background:\nSoft blurred background, warm ambient lighting, shallow depth of field\n\n"
                    "Long flowing dark hair, soft smile, natural makeup, warm skin tones\n\n"
                    "Outfit (casual chic):\nStylish fitted top, complementary bottoms, minimal accessories\n\n"
                    "Camera at eye level, medium shot, natural window light from the left, "
                    "realistic skin tones, candid feel" % theme
                )
            else:
                prompt = (
                    "Beautiful %s scene, atmospheric detail shot, "
                    "soft natural lighting, real photo, photorealistic" % theme
                )[:150]

            prompts.append({
                "asset_id": asset["asset_id"],
                "type": asset_type,
                "generator": generator,
                "content_format": res_info["format"],
                "resolution": res_info["resolution"],
                "prompt": prompt,
                "mood": "natural",
                "camera_framing": "medium",
                "prompt_hash": hashlib.md5(prompt.encode()).hexdigest()[:8],
            })

        return {"prompts": prompts}


# ── CLI ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 package_creative.py <plan.json> [scout_analysis.json]")
        print("  python3 package_creative.py demo")
        sys.exit(0)

    if sys.argv[1] == "demo":
        # Demo with mock plan
        creative = PackageCreative()
        mock_plan = {
            "package_id": "pkg_demo",
            "theme": "poolside luxury",
            "assets": [
                {"asset_id": "post_slide_1", "type": "post_slide", "generator": "z_image",
                 "has_character": True, "role": "hero_character",
                 "brief": "pool area; mood: relaxed; outfit: white bikini"},
                {"asset_id": "post_slide_2", "type": "post_slide", "generator": "z_image",
                 "has_character": True, "role": "character_scene",
                 "brief": "tropical garden; mood: serene"},
                {"asset_id": "post_slide_3", "type": "post_slide", "generator": "nano_banana",
                 "has_character": False, "role": "world_detail",
                 "brief": "sunset ocean; mood: dreamy"},
                {"asset_id": "story_frame_1", "type": "story_frame", "generator": "z_image",
                 "has_character": True, "role": "behind_the_scenes",
                 "brief": "poolside moment"},
            ],
        }

        result = creative.generate_prompts("pkg_demo", mock_plan)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        with open(sys.argv[1]) as f:
            plan = json.load(f)

        scout = None
        if len(sys.argv) > 2:
            with open(sys.argv[2]) as f:
                scout = json.load(f)

        creative = PackageCreative()
        result = creative.generate_prompts(
            plan.get("package_id", "pkg_cli"), plan, scout)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
