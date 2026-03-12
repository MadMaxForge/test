#!/usr/bin/env python3
"""
Creative Agent - reads Director brief + Scout analysis and generates
detailed image prompts for the Z-Image Turbo (w1man LoRA) endpoint.

Usage: python3 creative_agent.py <username> [--count N]
"""

import json
import os
import re
import sys
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

CREATIVE_SYSTEM_PROMPT = """You are Creative - an AI image prompt engineer specializing in photorealistic Instagram content for the character "w1man" (Lanna Danger).

You generate prompts for a Flux-based AI model (Z-Image Turbo) with these LoRAs:
- w1man (1.00) - main character trigger
- REDZ15 DetailDaemon (0.50) - detail enhancement
- Z-Breast-Slider (0.45) - body proportions

Image formats (agent MUST specify content_type for each prompt):
- "feed" = 1080x1350 (4:5) — for carousel/feed posts
- "story" = 1088x1920 (9:16) — for stories
- "reel" = 1088x1920 (9:16) — for reels (initial frame for Kling Motion Control)
- "square" = 1024x1024 (1:1) — for square posts

=== PROMPT TEMPLATE (MUST FOLLOW EXACTLY) ===

A w1man, {SETTING_DESCRIPTION}

Background:
{BACKGROUND_DESCRIPTION}

{APPEARANCE_DESCRIPTION}

Outfit ({OUTFIT_NAME}):
{OUTFIT_DESCRIPTION}

{CAMERA_AND_LIGHTING}

=== BLOCK DESCRIPTIONS ===

1. SETTING_DESCRIPTION: Place + atmosphere + general lighting.
   Examples: "in a cozy coffee shop with warm ambient lighting", "on a rooftop terrace at golden hour sunset"

2. BACKGROUND_DESCRIPTION: Background details + depth of field. Always include blur/bokeh for background elements.
   Examples: "Behind her is a brick wall with hanging plants, slightly blurred. To her left, a window with warm sunlight streaming in."

3. APPEARANCE_DESCRIPTION: Hair, facial expression, makeup. Be specific about emotion through face description.
   Examples: "She has long, straight black hair cascading down past her shoulders. Her expression is playful: bright smile, sparkling eyes looking at the camera, soft natural makeup."

4. OUTFIT_NAME: Short name for the look (meta-tag).
   Examples: "casual streetwear", "gym outfit", "evening dress"

5. OUTFIT_DESCRIPTION: Every clothing element separately with color, material, fit.
   Examples: "She is wearing a fitted black crop top with a small logo, high-waisted light blue jeans with subtle distressing at the knees, white sneakers. The fabric is cotton, casual and relaxed fit."

6. CAMERA_AND_LIGHTING: Camera position, framing, focal length, photo style, lighting setup.
   Examples: "Camera is directly in front of her, eye-level framing, candid amateur photo look. Lighting: warm natural window light from the left, soft shadows, realistic skin tones."

=== RULES ===
- ALWAYS start with "A w1man, " - without this the LoRA will not activate
- English only
- Clothing described in maximum detail - each element separately (top, bottom, shoes, accessories) with color, material, fit
- Background always with blur - add "slightly blurred", "shallow depth of field", "out of focus"
- Camera - specify: position (front/side/above), level (eye-level/chest-level), style (candid/professional/selfie)
- Emotion - through face description: "soft smile", "playful grin", "serious gaze", "confident expression"
- Do NOT use negative prompts, SD1.5-style quality tags, or weight brackets
- Optimal length: 150-400 words per prompt. The model understands long descriptions well
- Do NOT use markdown formatting inside prompts - just plain descriptive text

=== CAROUSEL STRUCTURE (CRITICAL) ===

Your prompts form ONE carousel post. The carousel structure MUST mirror the reference carousel from the Scout analysis.

Analyze the individual_analyses from Scout carefully:
- If the reference carousel has the SAME location across slides -> keep your location the same
- If the reference carousel CHANGES locations between slides -> change yours too in a similar pattern
- If the reference keeps the same outfit -> keep yours the same
- If the reference changes outfits -> change yours similarly
- Mirror whatever pattern the original carousel uses for pose, lighting, mood

The goal is to recreate the same TYPE of carousel (same photoshoot vs mixed content) but with our character.
Each prompt = one slide in the carousel. Match the reference's slide-by-slide structure.

=== KLING MOTION CONTROL AWARENESS (FOR REELS) ===

When generating prompts for reels (content_type="reel"), the image will be used as the
INITIAL FRAME for Kling Motion Control video generation. A reference video provides the
motion that will be transferred onto our character.

CRITICAL RULES for reel initial frames:
1. CAMERA DISTANCE MUST MATCH the reference video. If the reference shows a close-up
   (face/shoulders), generate a close-up. If it shows full body, generate full body.
   Mismatch = bad motion transfer (e.g. full body motion applied to close-up = broken result).
2. CHARACTER ORIENTATION should match. If reference person faces camera -> our character faces camera.
   If reference is side view -> generate side view.
3. POSE should be similar to the FIRST FRAME of the reference video (standing, sitting, etc.)
4. Keep background simple and clean — motion control works better with less visual noise.
5. Specify camera distance explicitly in the prompt: "close-up portrait from chest up",
   "medium shot from waist up", "full body shot", etc.

=== OUTPUT FORMAT ===

Output ONLY a valid JSON object (no markdown, no code fences):
{
  "carousel_theme": "short theme description for this carousel set",
  "reference_pattern": "description of what the reference carousel does (same location or changing, same outfit or changing, etc.)",
  "content_type": "feed or story or reel",
  "prompts": [
    {
      "id": 1,
      "concept": "short concept name",
      "outfit_name": "outfit category",
      "content_type": "feed or story or reel",
      "prompt": "A w1man, full structured prompt here following the template...",
      "mood": "mood description",
      "camera_framing": "close-up / medium shot / full body",
      "mirrors_reference_slide": "which reference slide this mirrors and how"
    }
  ]
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences."""


def parse_json_response(text):
    """Robustly parse JSON from LLM response."""
    if text is None:
        print("[ERROR] Received None response from API")
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r'''```(?:json)?\s*(\{[\s\S]*?\})\s*```''', text)
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
            last_complete = max(candidate.rfind('",'), candidate.rfind('"],'), candidate.rfind('},'))
            if last_complete > 0:
                candidate = candidate[:last_complete + 1]
            candidate += "]" * max(0, open_a) + "}" * max(0, open_b)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    print(f"[ERROR] Could not parse JSON from response:\n{text[:500]}")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 creative_agent.py <username> [--count N]")
        sys.exit(1)

    username = sys.argv[1]

    num_prompts = 4
    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        num_prompts = int(sys.argv[idx + 1])

    print(f"[Creative] Generating {num_prompts} prompts based on @{username} style...")

    brief_path = os.path.join(WORKSPACE, "director_briefs", f"{username}_brief.json")
    if not os.path.exists(brief_path):
        print(f"[ERROR] Director brief not found: {brief_path}")
        sys.exit(1)

    with open(brief_path) as f:
        brief = json.load(f)
    print("[Creative] Loaded Director brief")

    analysis_path = os.path.join(WORKSPACE, "scout_analysis", f"{username}_analysis.json")
    analysis = {}
    if os.path.exists(analysis_path):
        with open(analysis_path) as f:
            analysis = json.load(f)
        print("[Creative] Loaded Scout analysis")
    else:
        print("[Creative] No Scout analysis found, using brief only")

    brief_text = json.dumps(brief, indent=2, ensure_ascii=False)
    analysis_text = json.dumps(analysis, indent=2, ensure_ascii=False) if analysis else "Not available"

    prompt = (
        "Based on the following Instagram strategy brief and profile analysis,\n"
        f"generate exactly {num_prompts} detailed image generation prompts for the w1man character (Lanna Danger).\n\n"
        "These prompts will form ONE carousel post for Instagram.\n\n"
        "=== CAROUSEL STRUCTURE RULES (CRITICAL) ===\n"
        "Look at the Scout analysis individual_analyses - these are the REFERENCE slides.\n"
        "Your carousel MUST mirror the reference carousel's structure:\n\n"
        "1. Study what CHANGES vs what STAYS THE SAME between reference slides:\n"
        "   - Does the location change between slides? If yes, change yours too.\n"
        "   - Does the outfit stay the same? If yes, keep yours the same.\n"
        "   - Does the pose/angle vary? Mirror that variation pattern.\n"
        "   - Does the lighting/mood shift? Follow the same progression.\n\n"
        "2. Each of your prompts = one slide, mirroring the corresponding reference slide.\n"
        "   Prompt 1 mirrors reference slide 1, prompt 2 mirrors slide 2, etc.\n\n"
        "3. Adapt the content for our character (w1man/Lanna Danger) but keep the\n"
        "   same structural pattern (location changes, outfit changes, pose flow).\n\n"
        "Follow the block template EXACTLY for each prompt:\n"
        "  A w1man, [setting]\n"
        "  Background: [bg details with blur]\n"
        "  [appearance: hair, face, expression, makeup]\n"
        "  Outfit ([name]): [detailed clothing description]\n"
        "  [camera position, framing, lighting]\n\n"
        "Each prompt should be 150-400 words.\n\n"
        "=== DIRECTOR BRIEF ===\n"
        f"{brief_text}\n\n"
        "=== SCOUT ANALYSIS (with individual slide analyses) ===\n"
        f"{analysis_text}\n\n"
        f"Generate exactly {num_prompts} prompts for ONE carousel.\n"
        "Mirror the reference carousel's slide-by-slide structure.\n"
        "For each prompt, note which reference slide it mirrors.\n"
        "Output ONLY the JSON object. No markdown."
    )

    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        sys.exit(1)

    print(f"[*] Calling {MODEL} via OpenRouter...")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": CREATIVE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 8000,
        },
        timeout=180,
    )

    if resp.status_code != 200:
        print(f"[ERROR] API returned {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        print(f"[ERROR] No choices in API response: {str(data)[:500]}")
        sys.exit(1)
    content = choices[0].get("message", {}).get("content")
    if not content:
        print(f"[ERROR] Empty content in API response: {str(data)[:500]}")
        sys.exit(1)
    creative_output = parse_json_response(content)
    if creative_output is None:
        print("[ERROR] Failed to parse creative output JSON")
        sys.exit(1)

    creative_output["generated_at"] = datetime.now(timezone.utc).isoformat()
    creative_output["username"] = username
    creative_output["character"] = "w1man"

    for p in creative_output.get("prompts", []):
        if not p.get("prompt", "").startswith("A w1man"):
            p["prompt"] = "A w1man, " + p.get("prompt", "")

    creative_output["total_prompts"] = len(creative_output.get("prompts", []))

    # Ensure each prompt has content_type (default to carousel-level or "feed")
    default_ct = creative_output.get("content_type", "feed")
    for p in creative_output.get("prompts", []):
        if "content_type" not in p:
            p["content_type"] = default_ct

    output_dir = os.path.join(WORKSPACE, "creative_prompts")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{username}_prompts.json")

    with open(output_path, "w") as f:
        json.dump(creative_output, f, indent=2, ensure_ascii=False)

    print(f"[Creative] Saved {creative_output['total_prompts']} prompts: {output_path}")
    for i, p in enumerate(creative_output.get("prompts", [])):
        concept = p.get("concept", "N/A")
        prompt_text = p.get("prompt", "")[:100]
        print(f"  [{i + 1}] {concept}: {prompt_text}...")

    return output_path


if __name__ == "__main__":
    main()
