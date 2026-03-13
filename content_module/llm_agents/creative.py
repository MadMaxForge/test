"""
Creative — LLM-powered prompt generation for image generators.

Writes prompts for:
  - Z-Image (character): follows strict w1man LoRA template
  - Nano Banana (world): descriptive photography prompts
  - Reel start frames: simpler Z-Image prompts based on reference analysis

Does NOT change the plan or choose generators.
"""

import json
import re
import requests

from content_module.core.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    MODEL_CREATIVE,
    ZIMAGE_TRIGGER,
    ZIMAGE_PROMPT_TEMPLATE,
    DEFAULT_CHARACTER,
)


def _call_llm(messages: list[dict], temperature: float = 0.7, max_tokens: int = 3000) -> str:
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY not set")

    resp = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL_CREATIVE,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise Exception(f"OpenRouter API error ({resp.status_code}): {resp.text[:500]}")

    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")


def _parse_json(text: str) -> list:
    """Parse JSON array from response."""
    json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))
    # Try array
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    # Try object
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        parsed = json.loads(json_match.group())
        if "prompts" in parsed:
            return parsed["prompts"]
        return [parsed]
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def generate_prompts(plan: dict, analysis: dict) -> list[dict]:
    """
    Generate prompts for all assets in a plan.

    Args:
        plan: Content plan from planner
        analysis: Reference analysis from analyst

    Returns:
        List of prompt dicts, one per asset
    """
    content_type = plan.get("content_type", "post")
    theme = plan.get("theme", "lifestyle")
    assets = plan.get("assets", [])

    if not assets:
        return []

    # Build prompt generation request
    z_image_assets = []
    nano_assets = []
    reel_assets = []

    for asset in assets:
        has_char = asset.get("has_character", False)
        role = asset.get("role", "")
        generator = asset.get("generator", "")

        if generator == "kling" or role == "motion_reference" or generator == "user_provided":
            reel_assets.append(asset)
        elif has_char:
            z_image_assets.append(asset)
        else:
            nano_assets.append(asset)

    prompts = []

    # Generate Z-Image prompts (character)
    if z_image_assets:
        z_prompts = _generate_zimage_prompts(z_image_assets, theme, content_type, analysis)
        prompts.extend(z_prompts)

    # Generate Nano Banana prompts (world)
    if nano_assets:
        n_prompts = _generate_nano_prompts(nano_assets, theme, content_type, analysis)
        prompts.extend(n_prompts)

    # Reel assets don't need separate prompts here
    # (start_frame is z_image, motion_ref is user-provided, final is kling)
    for asset in reel_assets:
        role = asset.get("role", "")
        if role in ("motion_reference", "final_render"):
            prompts.append({
                "index": asset["index"],
                "role": role,
                "generator": asset.get("generator", "kling"),
                "prompt": "",
                "note": f"{role} — handled separately",
            })

    # Sort by index
    prompts.sort(key=lambda p: p.get("index", 0))
    return prompts


def _generate_zimage_prompts(
    assets: list[dict],
    theme: str,
    content_type: str,
    analysis: dict,
) -> list[dict]:
    """Generate Z-Image prompts for character assets."""
    slides_context = ""
    if analysis.get("slides"):
        slides_context = f"\nReference slide details:\n{json.dumps(analysis['slides'], indent=2)}\n"

    format_note = "4:5 feed format" if content_type == "post" else "9:16 vertical format"

    system_prompt = (
        "You are a prompt engineer for Z-Image Turbo, a photorealistic AI image generator.\n\n"
        "CRITICAL RULES:\n"
        f"1. Every prompt MUST start with exactly: {ZIMAGE_TRIGGER}\n"
        "2. Every prompt MUST include explicit female descriptors: 'She', 'young woman', 'her'\n"
        "3. Language: English only\n"
        "4. No negative prompts, no SD1.5-style tags, no weight brackets\n"
        "5. Describe outfit in detail: each piece, color, material, fit\n"
        "6. Background elements should mention 'slightly blurred' or 'shallow depth of field'\n"
        "7. Camera: specify position, level, style (candid/professional/selfie)\n"
        "8. Emotion: describe through face (soft smile, playful grin, etc.)\n"
        "9. Optimal length: 150-400 words per prompt\n\n"
        f"Prompt template:\n{ZIMAGE_PROMPT_TEMPLATE}\n\n"
        f"Default character appearance: {DEFAULT_CHARACTER['appearance']}\n"
    )

    asset_descriptions = []
    for a in assets:
        asset_descriptions.append(
            f"Asset {a['index']} (role: {a.get('role', '?')}): {a.get('description', '')}"
        )

    user_prompt = (
        f"Generate Z-Image prompts for {len(assets)} character image(s).\n\n"
        f"Content type: {content_type} ({format_note})\n"
        f"Theme: {theme}\n"
        f"{slides_context}\n"
        f"Assets to generate prompts for:\n" +
        "\n".join(asset_descriptions) +
        "\n\nReturn a JSON array where each element has:\n"
        '{"index": N, "role": "...", "generator": "z_image", "prompt": "A w1man, ..."}\n\n'
        "Output ONLY a JSON array. No markdown wrapping."
    )

    response_text = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    result = _parse_json(response_text)
    prompts = []

    if isinstance(result, list):
        for item in result:
            prompt_text = item.get("prompt", "")
            # Enforce w1man trigger
            if not prompt_text.startswith(ZIMAGE_TRIGGER):
                prompt_text = f"{ZIMAGE_TRIGGER} {prompt_text}"
            prompts.append({
                "index": item.get("index", 0),
                "role": item.get("role", ""),
                "generator": "z_image",
                "prompt": prompt_text,
            })
    elif isinstance(result, dict):
        prompt_text = result.get("prompt", "")
        if not prompt_text.startswith(ZIMAGE_TRIGGER):
            prompt_text = f"{ZIMAGE_TRIGGER} {prompt_text}"
        prompts.append({
            "index": result.get("index", assets[0]["index"]),
            "role": result.get("role", assets[0].get("role", "")),
            "generator": "z_image",
            "prompt": prompt_text,
        })

    print(f"[Creative] Generated {len(prompts)} Z-Image prompt(s)")
    return prompts


def _generate_nano_prompts(
    assets: list[dict],
    theme: str,
    content_type: str,
    analysis: dict,
) -> list[dict]:
    """Generate Nano Banana prompts for world/environment assets."""
    slides_context = ""
    if analysis.get("slides"):
        slides_context = f"\nReference slide details:\n{json.dumps(analysis['slides'], indent=2)}\n"

    format_note = "4:5 feed format" if content_type == "post" else "9:16 vertical format"

    system_prompt = (
        "You are a prompt engineer for Nano Banana 2, a photorealistic AI image generator.\n\n"
        "RULES:\n"
        "1. NO people/characters in these prompts — world/environment content only\n"
        "2. Focus on: interiors, objects, food, products, textures, landscapes, details\n"
        "3. Describe like professional photography: camera, lens, lighting, composition\n"
        "4. Be specific about materials, colors, textures\n"
        "5. Include technical photography terms: depth of field, color temperature, etc.\n"
        "6. Length: 100-300 words per prompt\n"
    )

    asset_descriptions = []
    for a in assets:
        asset_descriptions.append(
            f"Asset {a['index']} (role: {a.get('role', '?')}): {a.get('description', '')}"
        )

    user_prompt = (
        f"Generate Nano Banana prompts for {len(assets)} world/environment image(s).\n\n"
        f"Content type: {content_type} ({format_note})\n"
        f"Theme: {theme}\n"
        f"{slides_context}\n"
        f"Assets to generate prompts for:\n" +
        "\n".join(asset_descriptions) +
        "\n\nReturn a JSON array where each element has:\n"
        '{"index": N, "role": "...", "generator": "nano_banana", "prompt": "..."}\n\n'
        "Output ONLY a JSON array. No markdown wrapping."
    )

    response_text = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    result = _parse_json(response_text)
    prompts = []

    if isinstance(result, list):
        for item in result:
            prompts.append({
                "index": item.get("index", 0),
                "role": item.get("role", ""),
                "generator": "nano_banana",
                "prompt": item.get("prompt", ""),
            })
    elif isinstance(result, dict):
        prompts.append({
            "index": result.get("index", assets[0]["index"]),
            "role": result.get("role", assets[0].get("role", "")),
            "generator": "nano_banana",
            "prompt": result.get("prompt", ""),
        })

    print(f"[Creative] Generated {len(prompts)} Nano Banana prompt(s)")
    return prompts
