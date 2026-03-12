#!/usr/bin/env python3
"""
Publish Agent - assembles carousel post from generated images.
Generates caption + exactly 5 hashtags via LLM.
Outputs a ready-to-publish post package.

Usage: python3 publish_agent.py <username>
"""

import json
import os
import re
import sys
import requests
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

PUBLISH_SYSTEM_PROMPT = """You are Publish - an Instagram caption and hashtag specialist for the character Lanna Danger.

You receive:
- The carousel theme and prompt details
- The QC report with scores
- The Scout analysis of the reference profile

Your job is to create:
1. A compelling Instagram caption (2-4 sentences, casual/confident tone, no emojis overload - max 2 emojis)
2. Exactly 5 relevant hashtags (no more, no less)

Caption style:
- First person voice (as Lanna Danger)
- Confident, playful, aspirational
- Match the mood of the photos
- Include a subtle call to action (question, statement that invites engagement)
- Keep it natural and authentic, not salesy

Hashtag rules:
- Exactly 5 hashtags, no more
- Mix of popular (1M+ posts) and niche tags
- Relevant to the content and aesthetic
- No banned or spammy hashtags

Output ONLY a valid JSON object:
{
  "caption": "the caption text",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
  "post_type": "carousel",
  "image_count": 4
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences."""


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

    print("[ERROR] Could not parse JSON from Publish response")
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 publish_agent.py <username>")
        sys.exit(1)

    username = sys.argv[1]
    print("[Publish] Assembling post for @%s..." % username)

    # Load QC report
    qc_path = os.path.join(WORKSPACE, "qc_reports", username + "_qc.json")
    qc_report = {}
    if os.path.exists(qc_path):
        with open(qc_path) as f:
            qc_report = json.load(f)
        print("[Publish] Loaded QC report: %d/%d passed, avg %.1f" % (
            qc_report.get("passed", 0),
            qc_report.get("total_images", 0),
            qc_report.get("average_score", 0)
        ))

    # Load creative prompts
    prompts_path = os.path.join(WORKSPACE, "creative_prompts", username + "_prompts.json")
    prompts_data = {}
    if os.path.exists(prompts_path):
        with open(prompts_path) as f:
            prompts_data = json.load(f)
        print("[Publish] Loaded creative prompts: %d prompts" % prompts_data.get("total_prompts", 0))

    # Load Scout analysis for context
    analysis_path = os.path.join(WORKSPACE, "scout_analysis", username + "_analysis.json")
    analysis = {}
    if os.path.exists(analysis_path):
        with open(analysis_path) as f:
            analysis = json.load(f)

    # Find passed images
    photos_dir = os.path.join(WORKSPACE, "output", "photos", username)
    if not os.path.exists(photos_dir):
        print("[ERROR] No photos directory: %s" % photos_dir)
        sys.exit(1)

    all_images = sorted(Path(photos_dir).glob("*.png"))
    if not all_images:
        all_images = sorted(Path(photos_dir).glob("*.jpg"))

    # Filter to only QC-passed images
    passed_images = []
    qc_results = qc_report.get("results", [])
    passed_names = set()
    for r in qc_results:
        if r.get("final_pass", False):
            passed_names.add(r.get("image", ""))

    if passed_names:
        for img in all_images:
            if img.name in passed_names:
                passed_images.append(img)
    else:
        passed_images = all_images  # If no QC data, use all

    if not passed_images:
        print("[ERROR] No images passed QC")
        sys.exit(1)

    print("[Publish] %d images passed QC and will be in carousel" % len(passed_images))

    # Generate caption and hashtags
    carousel_theme = prompts_data.get("carousel_theme", "luxury lifestyle")
    shared_elements = prompts_data.get("shared_elements", {})

    prompt_summaries = []
    for p in prompts_data.get("prompts", []):
        prompt_summaries.append({
            "concept": p.get("concept", ""),
            "mood": p.get("mood", ""),
            "outfit_name": p.get("outfit_name", ""),
        })

    llm_prompt = (
        "Create an Instagram caption and exactly 5 hashtags for this carousel post.\n\n"
        "Carousel theme: %s\n"
        "Number of images: %d\n"
        "Shared setting: %s\n"
        "Shared outfit: %s\n\n"
        "QC Report: %d/%d images passed, average score %.1f/10\n\n"
        "Prompt concepts:\n%s\n\n"
        "Reference profile style: %s\n\n"
        "Write a caption as Lanna Danger (first person, confident, playful).\n"
        "Exactly 5 hashtags. Output ONLY the JSON."
    ) % (
        carousel_theme,
        len(passed_images),
        shared_elements.get("setting", "luxury setting"),
        shared_elements.get("outfit", "stylish outfit"),
        qc_report.get("passed", len(passed_images)),
        qc_report.get("total_images", len(passed_images)),
        qc_report.get("average_score", 8.0),
        json.dumps(prompt_summaries, indent=2),
        analysis.get("visual_style", "luxury lifestyle aesthetic")[:300],
    )

    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        sys.exit(1)

    print("[*] Calling %s for caption generation..." % MODEL)
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": PUBLISH_SYSTEM_PROMPT},
                {"role": "user", "content": llm_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 8000,
        },
        timeout=180,
    )

    if resp.status_code != 200:
        print("[ERROR] API returned %d: %s" % (resp.status_code, resp.text[:300]))
        sys.exit(1)

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        print("[ERROR] No choices in response")
        sys.exit(1)

    content = choices[0].get("message", {}).get("content")
    caption_data = parse_json_response(content)

    if not caption_data:
        print("[ERROR] Failed to parse caption response")
        caption_data = {
            "caption": "Living my best life. What do you think?",
            "hashtags": ["#lifestyle", "#fashion", "#ootd", "#aesthetic", "#vibes"],
        }

    # Enforce exactly 5 hashtags
    hashtags = caption_data.get("hashtags", [])
    if len(hashtags) > 5:
        hashtags = hashtags[:5]
    caption_data["hashtags"] = hashtags

    # Assemble post package
    post_package = {
        "username": username,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "post_type": "carousel",
        "carousel_theme": carousel_theme,
        "caption": caption_data.get("caption", ""),
        "hashtags": caption_data.get("hashtags", []),
        "images": [
            {
                "filename": img.name,
                "path": str(img),
                "index": i + 1,
            }
            for i, img in enumerate(passed_images)
        ],
        "image_count": len(passed_images),
        "qc_summary": {
            "passed": qc_report.get("passed", 0),
            "total": qc_report.get("total_images", 0),
            "average_score": qc_report.get("average_score", 0),
        },
        "status": "ready_for_review",
    }

    # Save post package
    output_dir = os.path.join(WORKSPACE, "posts")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, "%s_post_%s.json" % (username, timestamp))

    with open(output_path, "w") as f:
        json.dump(post_package, f, indent=2, ensure_ascii=False)

    print("\n[Publish] === POST PACKAGE ===")
    print("[Publish] Theme: %s" % carousel_theme)
    print("[Publish] Images: %d" % len(passed_images))
    for img in passed_images:
        print("  - %s" % img.name)
    print("[Publish] Caption: %s" % caption_data.get("caption", "")[:150])
    print("[Publish] Hashtags: %s" % " ".join(caption_data.get("hashtags", [])))
    print("[Publish] QC: avg %.1f/10" % qc_report.get("average_score", 0))
    print("[Publish] Status: ready_for_review")
    print("[Publish] Saved: %s" % output_path)

    return output_path


if __name__ == "__main__":
    main()
