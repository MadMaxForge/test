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
import random
import hashlib
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

CREATIVE_SYSTEM_PROMPT = """You are Creative - an AI image prompt engineer specializing in photorealistic Instagram content for the character "w1man" (Lanna Danger).

You generate prompts for TWO different AI image generators:
1. Z-Image Turbo (w1man LoRA) — for CHARACTER photos (person in scene)
   - Trigger: prompt MUST start with "A w1man, "
   - LoRAs: w1man (1.00), REDZ15 DetailDaemon (0.50), Z-Breast-Slider (0.45)
   - Mark these prompts with "generator": "z_image"

2. Nano Banana 2 — for NON-CHARACTER photos (products, cosmetics, flat-lays, backgrounds, objects)
   - NO "A w1man" trigger — these are product/object shots
   - Style: PHOTOREALISTIC — must look like a REAL photo, not illustration or render
   - ALWAYS include realism keywords: "real photo", "photorealistic", "real textures", "no illustration"
   - Prompt length: 100-150 characters. Short and concise — the agent creates the prompt freely
   - Mark these prompts with "generator": "nano_banana"

Image formats (agent MUST specify content_type for each prompt):
- "feed" = 1080x1350 (4:5) — for carousel/feed posts
- "story" = 1088x1920 (9:16) — for stories
- "reel" = 1088x1920 (9:16) — for reels (initial frame for Kling Motion Control)
- "square" = 1024x1024 (1:1) — for square posts

=== Z-IMAGE PROMPT TEMPLATE (for character photos — MUST FOLLOW EXACTLY) ===

A w1man, {SETTING_DESCRIPTION}

Background:
{BACKGROUND_DESCRIPTION}

{APPEARANCE_DESCRIPTION}

Outfit ({OUTFIT_NAME}):
{OUTFIT_DESCRIPTION}

{CAMERA_AND_LIGHTING}

=== NANO BANANA PROMPTS (for product/object photos) ===

For Nano Banana prompts, you create your OWN prompt freely. No fixed template.
The prompt MUST be 100-150 characters long (short and concise).
MUST include realism keywords to ensure the result looks like a REAL photograph:
- Always add: "real photo, photorealistic" or "real textures, no illustration"
- Example: "Luxury red lipstick on marble surface, soft light, real photo, photorealistic, 8K detail"
- Example: "Iced coffee with cream swirl on white table, morning sun, real photo, no illustration"
- Example: "Gold earrings on velvet cushion, studio lighting, photorealistic, real textures"

=== BLOCK DESCRIPTIONS (Z-Image) ===

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
- For Z-Image prompts: ALWAYS start with "A w1man, " - without this the LoRA will not activate
- For Nano Banana prompts: NEVER start with "A w1man" - these are product/object shots without the character
- English only
- Clothing described in maximum detail - each element separately (top, bottom, shoes, accessories) with color, material, fit
- Background always with blur - add "slightly blurred", "shallow depth of field", "out of focus"
- Camera - specify: position (front/side/above), level (eye-level/chest-level), style (candid/professional/selfie)
- Emotion - through face description: "soft smile", "playful grin", "serious gaze", "confident expression"
- Do NOT use negative prompts, SD1.5-style quality tags, or weight brackets
- Z-Image prompt length: 150-400 words per prompt. The model understands long descriptions well
- Nano Banana prompt length: 100-150 CHARACTERS (short!). Must include "real photo" or "photorealistic"
- Do NOT use markdown formatting inside prompts - just plain descriptive text
- AVOID mirrors, reflective surfaces, and glass in backgrounds — these cause AI artifacts
- EACH prompt MUST be UNIQUE — different setting, different pose, different outfit details, different camera angle
  Do NOT reuse the same concepts or descriptions across prompts

=== CAROUSEL STRUCTURE (CRITICAL) ===

Your prompts form ONE carousel post. The carousel structure MUST mirror the reference carousel from the Scout analysis.

Analyze the individual_analyses from Scout carefully:
- If the reference carousel has the SAME location across slides -> keep your location the same
- If the reference carousel CHANGES locations between slides -> change yours too in a similar pattern
- If the reference keeps the same outfit -> keep yours the same
- If the reference changes outfits -> change yours similarly
- Mirror whatever pattern the original carousel uses for pose, lighting, mood

CARITAL: A carousel MUST mix content types like real Instagram posts:
- Some slides = character photos (Z-Image, "generator": "z_image", starts with "A w1man")
- Some slides = product/cosmetic/object photos (Nano Banana, "generator": "nano_banana", NO "A w1man")
- Example pattern for 4-slide carousel: character, character, product close-up, character
- Example for 6-slide: character, product, character, character, product, character
- The products/objects should be thematically connected to the character photos (e.g., the makeup she's wearing, the drink she's holding, the accessories visible in character shots)

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
      "generator": "z_image or nano_banana",
      "prompt": "A w1man, full structured prompt here following the template... OR product prompt for nano_banana",
      "mood": "mood description",
      "camera_framing": "close-up / medium shot / full body",
      "mirrors_reference_slide": "which reference slide this mirrors and how"
    }
  ]
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences.
IMPORTANT: At least 1 prompt in the carousel MUST use generator=nano_banana (product/object photo).
IMPORTANT: Each prompt must be UNIQUE — never repeat the same concept or description."""


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

    # Load learned patterns from memory
    memory_context = ""
    try:
        from agent_memory import AgentMemory
        mem = AgentMemory()
        content_type = "feed"  # default
        if "--content-type" in sys.argv:
            ct_idx = sys.argv.index("--content-type")
            content_type = sys.argv[ct_idx + 1]
        memory_context = mem.build_creative_context(content_type=content_type, max_examples=5)
        if memory_context:
            print("[Creative] Loaded %d chars of learned patterns from memory" % len(memory_context))
        else:
            print("[Creative] No learned patterns yet (first run)")
    except Exception as e:
        print("[Creative] Warning: Could not load memory: %s" % e)
        mem = None

    # Load previous prompts from memory for deduplication
    previous_prompts_text = ""
    try:
        prev_prompts_path = os.path.join(WORKSPACE, "creative_prompts", f"{username}_prompts.json")
        if os.path.exists(prev_prompts_path):
            with open(prev_prompts_path) as f:
                prev_data = json.load(f)
            prev_concepts = [p.get("concept", "") for p in prev_data.get("prompts", [])]
            if prev_concepts:
                previous_prompts_text = (
                    "\n\n=== PREVIOUSLY USED CONCEPTS (DO NOT REPEAT) ===\n"
                    "These concepts were already generated. You MUST create DIFFERENT ones:\n"
                    + "\n".join(f"- {c}" for c in prev_concepts)
                )
                print(f"[Creative] Found {len(prev_concepts)} previous concepts to avoid")
    except Exception as e:
        print(f"[Creative] Warning: Could not load previous prompts: {e}")

    # Random variation seed to ensure different outputs each run
    variation_seed = random.randint(1, 10000)
    variation_themes = [
        "luxurious elegance", "casual street style", "sporty active", "cozy home vibes",
        "glamorous night out", "boho chic", "minimalist aesthetic", "tropical vacation",
        "urban explorer", "vintage retro", "gym fitness", "coffee shop mood",
        "beach sunset", "rooftop city views", "garden botanical", "art gallery",
        "spa wellness", "shopping spree", "brunch date", "poolside relaxation",
    ]
    random_theme_hint = random.choice(variation_themes)

    prompt = (
        "Based on the following Instagram strategy brief and profile analysis,\n"
        f"generate exactly {num_prompts} detailed image generation prompts for the w1man character (Lanna Danger).\n\n"
        "These prompts will form ONE carousel post for Instagram.\n\n"
        f"VARIATION SEED: {variation_seed} (use this to inspire creative variation)\n"
        f"THEME HINT: Consider incorporating '{random_theme_hint}' vibes into this carousel.\n\n"
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
        "=== CAROUSEL CONTENT MIX (CRITICAL) ===\n"
        "The carousel MUST mix content types like real Instagram posts:\n"
        f"- Out of {num_prompts} slides, at least 1 MUST be a product/object photo (generator=nano_banana)\n"
        "- Product photos: cosmetics, accessories, drinks, objects related to the scene\n"
        "- Character photos (generator=z_image): use the block template below\n"
        "- Example for 4 slides: z_image, z_image, nano_banana, z_image\n\n"
        "Follow the block template EXACTLY for Z-Image character prompts:\n"
        "  A w1man, [setting]\n"
        "  Background: [bg details with blur]\n"
        "  [appearance: hair, face, expression, makeup]\n"
        "  Outfit ([name]): [detailed clothing description]\n"
        "  [camera position, framing, lighting]\n\n"
        "For Nano Banana product prompts:\n"
        "  Professional product photography of [product], shot with [camera], [lighting], [composition]\n\n"
        "Each prompt should be 150-400 words.\n\n"
        "=== DIRECTOR BRIEF ===\n"
        f"{brief_text}\n\n"
        "=== SCOUT ANALYSIS (with individual slide analyses) ===\n"
        f"{analysis_text}\n\n"
        f"Generate exactly {num_prompts} prompts for ONE carousel.\n"
        "Mirror the reference carousel's slide-by-side structure.\n"
        "For each prompt, specify generator (z_image or nano_banana).\n"
        "At least 1 prompt MUST use generator=nano_banana.\n"
        "AVOID mirrors, glass, and reflective surfaces in Z-Image backgrounds.\n"
        "Output ONLY the JSON object. No markdown."
    )

    # Add dedup context
    if previous_prompts_text:
        prompt += previous_prompts_text

    # Inject learned patterns if available
    if memory_context:
        prompt += (
            "\n\n=== LEARNED PATTERNS (from past generations) ===\n"
            "Use these to improve quality. Repeat what worked, avoid what failed:\n\n"
            + memory_context
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
            "temperature": 0.9,
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

    # Only prepend "A w1man" to z_image prompts, NOT to nano_banana prompts
    for p in creative_output.get("prompts", []):
        generator = p.get("generator", "z_image")  # default to z_image for backwards compat
        if generator == "z_image" and not p.get("prompt", "").startswith("A w1man"):
            p["prompt"] = "A w1man, " + p.get("prompt", "")
        elif generator == "nano_banana":
            # Ensure nano_banana prompts do NOT start with "A w1man"
            prompt_text = p.get("prompt", "")
            if prompt_text.startswith("A w1man, "):
                p["prompt"] = prompt_text[len("A w1man, "):]
            elif prompt_text.startswith("A w1man,"):
                p["prompt"] = prompt_text[len("A w1man,"):].lstrip()

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

    # Save generation to memory
    try:
        if mem is None:
            from agent_memory import AgentMemory
            mem = AgentMemory()
        for p in creative_output.get("prompts", []):
            mem.save_generation(
                target_account=os.environ.get("TARGET_ACCOUNT", "lanna.danger"),
                content_type=p.get("content_type", "feed"),
                prompt_text=p.get("prompt", ""),
                prompt_json=p,
                generation_tool="pending",
                source_username=username,
            )
        # Save style patterns from this generation
        for p in creative_output.get("prompts", []):
            mem.save_pattern(
                pattern_type="prompt_style",
                pattern_data={
                    "concept": p.get("concept", ""),
                    "content_type": p.get("content_type", "feed"),
                    "prompt_preview": p.get("prompt", "")[:300],
                },
                source_username=username,
                score=0.0,  # will be updated after QC
            )
        mem.log_event("creative", "prompts_generated", {
            "username": username,
            "count": creative_output.get("total_prompts", 0),
            "content_type": creative_output.get("content_type", "feed"),
        }, lesson="Generated %d prompts inspired by @%s" % (
            creative_output.get("total_prompts", 0), username))
        mem.close()
        print("[Creative] Saved generation history to memory")
    except Exception as e:
        print("[Creative] Warning: Could not save to memory: %s" % e)

    return output_path


if __name__ == "__main__":
    main()
