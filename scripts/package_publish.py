#!/usr/bin/env python3
"""
Package Publish Agent — generates captions, hashtags, story overlays for a package.

Takes a package state (with approved assets) and generates all text content:
- Post caption (2-4 sentences, conversational, 100-150 chars)
- Exactly 5 hashtags
- Story text overlays (2-4 words each, or null)
- Reel caption (1 short sentence)

Uses gemini-2.0-flash-lite for cost efficiency.

Usage:
    from package_publish import PackagePublish
    publisher = PackagePublish()
    result = publisher.generate_text(package_id, theme, assets, plan_summary)

CLI:
    python3 package_publish.py <state.json>
    python3 package_publish.py demo
"""

import json
import os
import re
import sys
import requests
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "google/gemini-2.0-flash-lite-001"

PUBLISH_SYSTEM_PROMPT = """You are Publish Agent for an Instagram influencer character (Lanna Danger).

You generate text content for a content package:
1. Post caption: 2-4 sentences, casual/confident tone, ends with question or CTA
2. Exactly 5 hashtags (mix popular + niche)
3. Story overlay text: 2-4 words max per story (or null if no text needed)
4. Reel caption: 1 short sentence (if package has reel)

Rules:
- Caption: 100-150 characters, first person voice
- NO emoji overload (max 2)
- Confident, playful, aspirational tone
- Hashtags: relevant to theme, no banned/spammy tags
- Story overlays: very short, like "living my best life" or "golden hour vibes"

Output ONLY valid JSON:
{
  "post": {
    "caption": "caption text",
    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
    "caption_length": 120
  },
  "stories": [
    {"asset_id": "story_frame_1", "text_overlay": "short text or null"}
  ],
  "reel": {"asset_id": "reel_1", "caption": "short reel caption", "hashtags": ["#tag1", "#tag2", "#tag3"]} or null
}

IMPORTANT: Output ONLY the JSON. No markdown."""


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

    print("[PackagePublish] Could not parse JSON: %s" % text[:300])
    return None


class PackagePublish:
    """Generates text content for a package."""

    def __init__(self, model=None, api_key=None):
        self.model = model or MODEL
        self.api_key = api_key or OPENROUTER_API_KEY

    def generate_text(self, package_id, theme, assets, plan_summary=None):
        """
        Generate all text content for a package.

        Args:
            package_id: str
            theme: str
            assets: list - asset dicts from state
            plan_summary: dict | None - summary from plan

        Returns:
            dict with post, stories, reel text
        """
        # Describe assets for context
        asset_desc = []
        story_assets = []
        reel_asset = None

        for asset in assets:
            role = "character" if asset.get("has_character") else "world"
            asset_desc.append("%s (%s, %s, %s)" % (
                asset["asset_id"], asset["type"], role,
                asset.get("role", "?")))

            if asset["type"] == "story_frame":
                story_assets.append(asset)
            elif asset["type"] == "reel_start_frame":
                reel_asset = asset

        user_prompt = (
            "Package theme: %s\n"
            "Total assets: %d\n"
            "Assets: %s\n\n" % (
                theme, len(assets), ", ".join(asset_desc)))

        if plan_summary:
            user_prompt += (
                "Summary: %d posts, %d stories, %d reels\n"
                "Character ratio: %.0f%%\n\n" % (
                    plan_summary.get("post_slides", 0),
                    plan_summary.get("story_frames", 0),
                    plan_summary.get("reel_start_frames", 0),
                    plan_summary.get("character_ratio", 0.65) * 100))

        user_prompt += "Generate caption, hashtags, story overlays"
        if reel_asset:
            user_prompt += ", and reel caption"
        user_prompt += ". Output ONLY JSON."

        print("[PackagePublish] Calling %s for text generation..." % self.model)

        result = self._call_llm(user_prompt)

        if not result:
            print("[PackagePublish] LLM failed, using fallback")
            result = self._build_fallback(theme, story_assets, reel_asset)

        # Post-process
        result = self._post_process(result, story_assets, reel_asset)

        # Add metadata
        result["publish_id"] = "publish_%s" % package_id
        result["package_id"] = package_id
        result["theme"] = theme
        result["created_at"] = datetime.now(timezone.utc).isoformat()

        print("[PackagePublish] Text generated for '%s'" % theme)
        return result

    def _call_llm(self, user_prompt):
        """Call OpenRouter API."""
        if not self.api_key:
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
                        {"role": "system", "content": PUBLISH_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
                timeout=60,
            )

            if resp.status_code != 200:
                print("[PackagePublish] API error %d: %s" % (
                    resp.status_code, resp.text[:200]))
                return None

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return parse_json_response(content) if content else None

        except Exception as e:
            print("[PackagePublish] Request error: %s" % str(e))
            return None

    def _post_process(self, result, story_assets, reel_asset):
        """Enforce rules on generated text."""
        # Enforce exactly 5 hashtags for post
        post = result.get("post", {})
        hashtags = post.get("hashtags", [])
        if len(hashtags) > 5:
            post["hashtags"] = hashtags[:5]
        elif len(hashtags) < 5:
            # Pad with generic tags
            generic = ["#lifestyle", "#mood", "#vibes", "#inspo", "#content"]
            while len(hashtags) < 5:
                for tag in generic:
                    if tag not in hashtags:
                        hashtags.append(tag)
                        break
                else:
                    break
            post["hashtags"] = hashtags[:5]

        # Ensure caption_length
        caption = post.get("caption", "")
        post["caption_length"] = len(caption)

        result["post"] = post

        # Ensure story overlays match actual story assets
        stories = result.get("stories", [])
        story_ids = {a["asset_id"] for a in story_assets}

        # Filter to only existing stories
        valid_stories = [s for s in stories if s.get("asset_id") in story_ids]

        # Add missing stories
        existing_ids = {s.get("asset_id") for s in valid_stories}
        for sa in story_assets:
            if sa["asset_id"] not in existing_ids:
                valid_stories.append({
                    "asset_id": sa["asset_id"],
                    "text_overlay": None,
                })

        result["stories"] = valid_stories

        # Handle reel
        if not reel_asset:
            result["reel"] = None
        elif result.get("reel") is None:
            result["reel"] = {
                "asset_id": reel_asset["asset_id"],
                "caption": "Living the moment.",
                "hashtags": ["#reels", "#mood", "#vibes"],
            }

        return result

    def _build_fallback(self, theme, story_assets, reel_asset):
        """Build fallback text if LLM fails."""
        result = {
            "post": {
                "caption": "Living the %s life. What's your vibe today?" % theme,
                "hashtags": ["#lifestyle", "#mood", "#%s" % theme.replace(" ", ""),
                             "#vibes", "#content"],
                "caption_length": 0,
            },
            "stories": [],
            "reel": None,
        }

        for sa in story_assets:
            result["stories"].append({
                "asset_id": sa["asset_id"],
                "text_overlay": None,
            })

        if reel_asset:
            result["reel"] = {
                "asset_id": reel_asset["asset_id"],
                "caption": "That %s feeling." % theme,
                "hashtags": ["#reels", "#mood", "#vibes"],
            }

        return result


# ── CLI ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 package_publish.py <state.json>")
        print("  python3 package_publish.py demo")
        sys.exit(0)

    if sys.argv[1] == "demo":
        publisher = PackagePublish()
        mock_assets = [
            {"asset_id": "post_slide_1", "type": "post_slide",
             "has_character": True, "role": "hero_character"},
            {"asset_id": "post_slide_2", "type": "post_slide",
             "has_character": True, "role": "character_scene"},
            {"asset_id": "post_slide_3", "type": "post_slide",
             "has_character": False, "role": "world_detail"},
            {"asset_id": "story_frame_1", "type": "story_frame",
             "has_character": True, "role": "behind_the_scenes"},
            {"asset_id": "reel_1", "type": "reel_start_frame",
             "has_character": True, "role": "motion_content"},
        ]
        result = publisher.generate_text(
            "pkg_demo", "poolside luxury", mock_assets,
            {"post_slides": 3, "story_frames": 1,
             "reel_start_frames": 1, "character_ratio": 0.67})
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        with open(sys.argv[1]) as f:
            state = json.load(f)
        publisher = PackagePublish()
        result = publisher.generate_text(
            state.get("package_id", "pkg_cli"),
            state.get("theme", "lifestyle"),
            state.get("assets", []))
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
