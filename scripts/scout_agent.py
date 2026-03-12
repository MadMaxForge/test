#!/usr/bin/env python3
"""
Scout Agent - analyzes Instagram profile using VISION on actual downloaded images.
Performs 6-element analysis: background, clothing, pose, lighting, camera angle, mood.
Uses OpenRouter API (Kimi K2.5 vision) for AI analysis.

Usage: python3 scout_agent.py <username> [--max-images N]
"""

import json
import os
import re
import sys
import base64
import requests
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

SCOUT_SYSTEM_PROMPT = """You are Scout - an Instagram visual style analyst agent.
You receive actual images from an Instagram profile and analyze them visually.

For EACH image you must analyze these 6 elements:
1. Background: setting, location type, colors, depth of field, bokeh
2. Clothing: every garment described (type, color, material, fit, brand hints)
3. Pose: body position, hand placement, stance, angle to camera
4. Lighting: type (natural/studio/mixed), direction, warmth, shadows, highlights
5. Camera angle: eye-level, above, below, distance (close-up/medium/full body), framing
6. Mood: overall feeling, expression, atmosphere, energy level

After analyzing individual images, provide an AGGREGATE style profile.

Output ONLY a valid JSON object (no markdown, no code fences) with this structure:
{
  "username": "...",
  "profile_summary": "2-3 sentence overview of visual style",
  "images_analyzed": 0,
  "individual_analyses": [
    {
      "image": "filename",
      "background": "description",
      "clothing": "description",
      "pose": "description",
      "lighting": "description",
      "camera_angle": "description",
      "mood": "description"
    }
  ],
  "aggregate_style": {
    "dominant_backgrounds": ["type1", "type2"],
    "clothing_style": "overall style description",
    "common_poses": ["pose1", "pose2"],
    "lighting_preference": "description",
    "camera_patterns": "description",
    "mood_palette": "description"
  },
  "content_themes": ["theme1", "theme2"],
  "visual_style": "comprehensive visual aesthetic description",
  "engagement_analysis": {
    "avg_likes": 0,
    "avg_comments": 0,
    "top_performing_type": "photo|carousel|video|reel",
    "engagement_rate_estimate": "high|medium|low"
  },
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["rec1", "rec2", "rec3"]
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences."""


def load_manifest(username):
    path = os.path.join(WORKSPACE, "downloads", username, "manifest.json")
    if not os.path.exists(path):
        print("[ERROR] Manifest not found: " + path)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def get_image_files(username, max_images=6):
    """Get list of image files for analysis (prefer slide1 and thumb images)."""
    img_dir = os.path.join(WORKSPACE, "downloads", username)
    all_files = sorted(Path(img_dir).glob("*.jpg"))
    image_files = [f for f in all_files if f.name not in ("manifest.json", "profile_pic.jpg")]

    priority = []
    others = []
    for f in image_files:
        if "_thumb.jpg" in f.name or "_slide1.jpg" in f.name:
            priority.append(f)
        else:
            others.append(f)

    selected = (priority + others)[:max_images]
    return selected


def encode_image_base64(filepath):
    """Read image file and return base64 encoded string."""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def build_vision_prompt(manifest, image_files):
    """Build a prompt with images for vision analysis."""
    profile = manifest.get("profile", {})
    posts = manifest.get("posts", [])

    text_part = "Analyze this Instagram profile's visual style based on the provided images.\n\n"
    text_part += "Profile:\n"
    text_part += "- Username: @" + profile.get("username", "unknown") + "\n"
    text_part += "- Full name: " + str(profile.get("full_name", "N/A")) + "\n"
    text_part += "- Bio: " + str(profile.get("biography", "N/A")) + "\n"
    text_part += "- Followers: " + str(profile.get("followers", "N/A")) + "\n"
    text_part += "- Following: " + str(profile.get("following", "N/A")) + "\n"
    text_part += "- Total posts: " + str(profile.get("post_count", "N/A")) + "\n"
    text_part += "- Verified: " + str(profile.get("is_verified", False)) + "\n"
    text_part += "- Category: " + str(profile.get("category", "N/A")) + "\n"
    text_part += "\nPost engagement data:\n"

    for i, post in enumerate(posts[:12]):
        ptype = post.get("post_type", "photo")
        likes = post.get("likes", 0)
        comments = post.get("comments", 0)
        text_part += "  Post %d: type=%s, likes=%s, comments=%s\n" % (i + 1, ptype, likes, comments)

    text_part += "\nI'm providing %d images from this profile.\n" % len(image_files)
    text_part += "For EACH image, analyze all 6 elements: background, clothing, pose, lighting, camera angle, mood.\n"
    text_part += "Then provide an aggregate style profile summarizing the overall visual patterns.\n\n"
    text_part += "Image filenames: " + ", ".join(f.name for f in image_files) + "\n\n"
    text_part += "Output ONLY the JSON analysis object. No markdown."

    return text_part


def parse_json_response(text):
    """Robustly parse JSON from LLM response."""
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

    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        candidate = m.group(1)
        open_b = candidate.count('{') - candidate.count('}')
        open_a = candidate.count('[') - candidate.count(']')
        if open_b > 0 or open_a > 0:
            last_complete = max(candidate.rfind('",'), candidate.rfind('"],'), candidate.rfind('},'))
            if last_complete > 0:
                candidate = candidate[:last_complete + 1]
            candidate += ']' * max(0, open_a) + '}' * max(0, open_b)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    print("[ERROR] Could not parse JSON from response:\n" + text[:500])
    sys.exit(1)


def call_openrouter_vision(text_prompt, image_files):
    """Call OpenRouter with vision model, sending images as base64."""
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        sys.exit(1)

    content_parts = [{"type": "text", "text": text_prompt}]

    for img_file in image_files:
        print("  [+] Encoding: " + img_file.name)
        b64 = encode_image_base64(img_file)
        content_parts.append({
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + b64
            }
        })

    messages = [
        {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
        {"role": "user", "content": content_parts},
    ]

    print("[*] Calling %s (vision) via OpenRouter with %d images..." % (MODEL, len(image_files)))
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 6000,
        },
        timeout=180,
    )

    if resp.status_code != 200:
        print("[ERROR] API returned %d: %s" % (resp.status_code, resp.text[:500]))
        sys.exit(1)

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return parse_json_response(content)


def call_openrouter_text(prompt):
    """Fallback: text-only analysis from manifest data."""
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        sys.exit(1)

    messages = [
        {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    print("[*] Calling %s (text-only fallback) via OpenRouter..." % MODEL)
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        print("[ERROR] API returned %d: %s" % (resp.status_code, resp.text[:500]))
        sys.exit(1)

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return parse_json_response(content)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scout_agent.py <username> [--max-images N] [--text-only]")
        sys.exit(1)

    username = sys.argv[1]
    max_images = 6
    text_only = "--text-only" in sys.argv

    if "--max-images" in sys.argv:
        idx = sys.argv.index("--max-images")
        max_images = int(sys.argv[idx + 1])

    print("[Scout] Analyzing @%s..." % username)

    manifest = load_manifest(username)
    print("[Scout] Loaded manifest: %s posts" % manifest.get("total_posts_scraped", "?"))

    image_files = get_image_files(username, max_images)
    print("[Scout] Found %d images for analysis" % len(image_files))

    if image_files and not text_only:
        print("[Scout] Using VISION analysis on actual images (6-element analysis)")
        text_prompt = build_vision_prompt(manifest, image_files)
        analysis = call_openrouter_vision(text_prompt, image_files)
        analysis["analysis_mode"] = "vision"
    else:
        print("[Scout] Using text-only analysis (no images available or --text-only flag)")
        profile = manifest.get("profile", {})
        posts = manifest.get("posts", [])
        prompt = "Analyze this Instagram profile:\n\nProfile:\n"
        prompt += "- Username: @" + profile.get("username", "unknown") + "\n"
        prompt += "- Followers: " + str(profile.get("followers", "N/A")) + "\n"
        prompt += "- Bio: " + str(profile.get("biography", "N/A")) + "\n\n"
        prompt += "Recent %d posts:\n" % len(posts)
        for i, post in enumerate(posts):
            ptype = post.get("post_type", "photo")
            likes = post.get("likes", 0)
            comments = post.get("comments", 0)
            prompt += "  Post %d: type=%s, likes=%s, comments=%s\n" % (i + 1, ptype, likes, comments)
        prompt += "\nOutput ONLY the JSON analysis object. No markdown."
        analysis = call_openrouter_text(prompt)
        analysis["analysis_mode"] = "text_only"

    analysis["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    analysis["images_analyzed"] = len(image_files) if not text_only else 0

    output_dir = os.path.join(WORKSPACE, "scout_analysis")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "%s_analysis.json" % username)

    with open(output_path, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print("[Scout] Analysis saved: " + output_path)
    summary = analysis.get("profile_summary", analysis.get("visual_style", "N/A"))
    print("[Scout] Summary: " + str(summary)[:200])


if __name__ == "__main__":
    main()
