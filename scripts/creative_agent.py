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

CREATIVE_SYSTEM_PROMPT = """You are Creative - an AI image prompt engineer for the Instagram character "w1man" (Lanna Danger).

You generate prompts for TWO generators:
1. Z-Image Turbo (w1man LoRA) — CHARACTER photos where the person's FACE is visible
   - Trigger: prompt MUST start with "A w1man, "
   - Mark with "generator": "z_image"

2. Nano Banana 2 — NON-CHARACTER photos: landscapes, objects, vehicles, buildings, food, details
   - NO "A w1man" — these shots have NO person or the person's face is NOT visible
   - PHOTOREALISTIC: include "real photo", "photorealistic", "no illustration"
   - Prompt length: 100-150 characters (short!)
   - Mark with "generator": "nano_banana"

Content types (MUST specify for each prompt):
- "feed" = 1080x1350 (4:5) — carousel/feed posts
- "story" = 1088x1920 (9:16) — stories
- "reel" = 1088x1920 (9:16) — reels

=== Z-IMAGE PROMPT TEMPLATE (MUST FOLLOW) ===

A w1man, {SETTING_DESCRIPTION}

Background:
{BACKGROUND_DESCRIPTION}

{APPEARANCE_DESCRIPTION}

Outfit ({OUTFIT_NAME}):
{OUTFIT_DESCRIPTION}

{CAMERA_AND_LIGHTING}

Block descriptions:
- SETTING: Place + atmosphere + general lighting
- BACKGROUND: Details + depth of field + blur/bokeh
- APPEARANCE: Hair, expression, makeup. Emotion through face: "soft smile", "playful grin", etc.
- OUTFIT_NAME: Short meta-tag ("casual streetwear", "bikini set", etc.)
- OUTFIT: Every element separately — type, color, material, fit
- CAMERA: Position, framing, focal length, lighting setup

=== NANO BANANA PROMPTS (detail/landscape shots) ===

Free-form prompt, 100-150 characters. MUST include "real photo" or "photorealistic".
Examples:
- "Wooden log cabin by frozen lake, pine trees, fresh snow, golden hour, real photo, photorealistic"
- "Bowl of fresh cherries on sunlit legs by pool, overhead view, real photo, no illustration"
- "Blue ATV on muddy lakeshore, forest background, overcast sky, real photo, photorealistic"

=== CAROUSEL STRUCTURE (CRITICAL — READ CAREFULLY) ===

You create ONE carousel post. Study the Scout analysis to understand the reference carousel.

RULE 1 — VARIABLE SIZE: Carousels have 2-4 photos (NOT always 4!).
  Decide how many based on the reference. 2 photos is fine. 3 is fine. 4 is fine.

RULE 2 — ONE THEME: All photos share ONE atmosphere/setting/mood.
  Examples: "winter cabin", "poolside luxury", "Miami beach", "summer cottage".
  The THEME stays constant. Only poses, angles, moments change.

RULE 3 — MIX OF GENERATORS:
  - Slides where the character's FACE is clearly visible → Z-Image (generator: z_image)
  - Slides showing landscapes, objects, buildings, food, vehicles, or body without face → Nano Banana (generator: nano_banana)
  - NOT every carousel needs Nano Banana. If all slides show the face → all Z-Image is OK.
  - The MIX should feel natural, like a real Instagram carousel.

RULE 4 — OUTFIT CAN CHANGE within the same theme.
  Example: Winter carousel → slide 1 blue parka, slide 2 pink outfit with white hat.
  Same winter atmosphere, different outfit. This is normal.

RULE 5 — NARRATIVE FLOW: Photos tell a mini-story.
  Example: Arrive at cottage → pose in nature → detail shot of ATV → travel shot on train.
  The carousel should feel like moments from one experience.

RULE 6 — MIRROR THE REFERENCE:
  Study Scout's individual_analyses. Your carousel mirrors the reference pattern:
  - Same location across slides? → Keep yours the same.
  - Different angles/poses? → Vary yours similarly.
  - Has a landscape/object slide? → Include one too.
  - All face shots? → OK to make all Z-Image.

=== REAL CAROUSEL EXAMPLES (from @lanna.danger) ===

Carousel A (2 photos, "Miami Beach"): Both Z-Image, same orange bikini, same balcony.
  Slide 1: full body frontal, leaning on railing. Slide 2: side angle, looking away.

Carousel B (3 photos, "Winter Snow"): 2x Z-Image + 1x Nano Banana.
  Slide 1: Z-Image — blue parka, holding snowball, laughing.
  Slide 2: Z-Image — pink outfit + white hat, kneeling in snow (DIFFERENT outfit, same theme!).
  Slide 3: Nano Banana — log cabin with snowmobile, no person.

Carousel C (2 photos, "Poolside"): 1x Nano Banana + 1x Z-Image.
  Slide 1: Nano Banana — cherries in bowl on legs, no face.
  Slide 2: Z-Image — leopard bikini, wine glass, villa garden.

Carousel D (3 photos, "Wine by the Pool"): All Z-Image.
  Slide 1: full body by pool, wine glass. Slide 2: closer, wine + touching hair.
  Slide 3: close-up portrait by pool.

Carousel E (4 photos, "Summer Cottage"): 2x Nano Banana + 2x Z-Image.
  Slide 1: Nano Banana — log cabin by lake.
  Slide 2: Z-Image — black swimsuit, dandelion field.
  Slide 3: Nano Banana — ATV by lake.
  Slide 4: Z-Image — white top, sitting in train.

=== RULES ===
- Z-Image: ALWAYS start with "A w1man, " — LoRA won't activate without it
- Nano Banana: NEVER start with "A w1man" — no character in these
- English only
- Z-Image prompt: 150-400 words. Nano Banana: 100-150 CHARACTERS
- AVOID mirrors, reflective surfaces, glass in backgrounds (AI artifacts)
- Each prompt UNIQUE — different pose, angle, moment
- Do NOT use negative prompts, SD1.5 quality tags, or weight brackets

=== KLING MOTION CONTROL (FOR REELS ONLY) ===

When content_type="reel", the image = initial frame for Kling video generation.
- Camera distance MUST match reference video (close-up vs full body)
- Character orientation should match (facing camera, side view, etc.)
- Simple background (less noise = better motion transfer)

=== OUTPUT FORMAT ===

Output ONLY valid JSON (no markdown, no code fences):
{
  "carousel_theme": "short theme",
  "slide_count": 2-4,
  "reference_pattern": "what the reference carousel does",
  "content_type": "feed or story or reel",
  "prompts": [
    {
      "id": 1,
      "concept": "short concept",
      "outfit_name": "outfit category",
      "content_type": "feed",
      "generator": "z_image or nano_banana",
      "prompt": "full prompt text",
      "mood": "mood",
      "camera_framing": "close-up / medium / full body",
      "mirrors_reference_slide": "which slide this mirrors"
    }
  ]
}

IMPORTANT: Output ONLY the JSON. No text before or after.
IMPORTANT: slide_count and number of prompts MUST match (2-4 prompts).
IMPORTANT: Each prompt UNIQUE — never repeat concepts."""


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

    num_prompts = None  # Let the agent decide (2-4) based on reference carousel
    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        num_prompts = int(sys.argv[idx + 1])

    if num_prompts:
        print(f"[Creative] Generating {num_prompts} prompts based on @{username} style...")
    else:
        print(f"[Creative] Generating carousel prompts (2-4, agent decides) based on @{username} style...")

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

    # Load ALL previous concepts from memory DB for cumulative deduplication
    previous_prompts_text = ""
    try:
        all_past_concepts = []
        # First: try memory DB (cumulative across all runs)
        if mem and mem.conn:
            all_past_concepts = mem.get_all_past_concepts(source_username=username, limit=50)
            if all_past_concepts:
                print(f"[Creative] Found {len(all_past_concepts)} past concepts in memory DB")
        # Fallback: read last prompts file if DB returned nothing
        if not all_past_concepts:
            prev_prompts_path = os.path.join(WORKSPACE, "creative_prompts", f"{username}_prompts.json")
            if os.path.exists(prev_prompts_path):
                with open(prev_prompts_path) as f:
                    prev_data = json.load(f)
                all_past_concepts = [p.get("concept", "") for p in prev_data.get("prompts", []) if p.get("concept")]
                if all_past_concepts:
                    print(f"[Creative] Found {len(all_past_concepts)} past concepts from last file (DB empty)")
        if all_past_concepts:
            previous_prompts_text = (
                "\n\n=== PREVIOUSLY USED CONCEPTS (DO NOT REPEAT) ===\n"
                "These concepts were already generated in past runs. You MUST create COMPLETELY DIFFERENT ones:\n"
                + "\n".join(f"- {c}" for c in all_past_concepts)
            )
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

    count_instruction = f"generate exactly {num_prompts}" if num_prompts else "generate 2 to 4 (you decide based on the reference)"

    prompt = (
        "Based on the following Instagram strategy brief and profile analysis,\n"
        f"{count_instruction} detailed image generation prompts for the w1man character (Lanna Danger).\n\n"
        "These prompts will form ONE carousel post for Instagram.\n\n"
        f"VARIATION SEED: {variation_seed} (use this to inspire creative variation)\n"
        f"THEME HINT: Consider incorporating '{random_theme_hint}' vibes into this carousel.\n\n"
        "=== CAROUSEL RULES (CRITICAL) ===\n"
        "1. Study the Scout analysis individual_analyses — these are the REFERENCE slides.\n"
        "2. Decide how many slides YOUR carousel needs (2-4). Mirror the reference count if possible.\n"
        "3. ALL slides share ONE theme/atmosphere (same location, season, mood).\n"
        "   Only poses, angles, emotions, and moments change between slides.\n"
        "4. Outfits CAN change within the same theme (e.g. blue parka slide 1, pink outfit slide 2 — both winter).\n\n"
        "=== GENERATOR SELECTION ===\n"
        "For each slide, decide the generator based on WHAT the slide shows:\n"
        "- Face visible? → Z-Image (generator=z_image, starts with 'A w1man, ')\n"
        "- Landscape, building, object, vehicle, food, or body without face? → Nano Banana (generator=nano_banana)\n"
        "- If ALL reference slides show the face → ALL Z-Image is fine (no forced nano_banana)\n"
        "- If reference has detail/landscape shots → include Nano Banana slides too\n\n"
        "=== NARRATIVE FLOW ===\n"
        "The slides should tell a mini-story — moments from one experience:\n"
        "Example: Arrive at location → pose there → detail shot → another moment\n\n"
        "Follow the block template EXACTLY for Z-Image character prompts:\n"
        "  A w1man, [setting]\n"
        "  Background: [bg details with blur]\n"
        "  [appearance: hair, face, expression, makeup]\n"
        "  Outfit ([name]): [detailed clothing description]\n"
        "  [camera position, framing, lighting]\n\n"
        "For Nano Banana prompts (100-150 chars):\n"
        "  [scene/object description], [lighting], real photo, photorealistic\n\n"
        "Z-Image prompt: 150-400 words. Nano Banana: 100-150 CHARACTERS (short!).\n\n"
        "=== DIRECTOR BRIEF ===\n"
        f"{brief_text}\n\n"
        "=== SCOUT ANALYSIS (with individual slide analyses) ===\n"
        f"{analysis_text}\n\n"
        f"{count_instruction} prompts for ONE carousel.\n"
        "All prompts share ONE theme. Only poses/angles/moments change.\n"
        "Mirror the reference carousel's pattern (face shots vs detail shots).\n"
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
