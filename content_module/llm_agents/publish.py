"""
Publish — LLM-powered text generation for approved content.

Generates:
  - Post captions + hashtags
  - Story overlay text
  - Reel captions

Called AFTER visual content is approved, not before.
"""

import json
import re
import requests

from content_module.core.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    MODEL_PUBLISH,
)


def _call_llm(messages: list[dict], temperature: float = 0.6, max_tokens: int = 1000) -> str:
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY not set")

    resp = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL_PUBLISH,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )

    if resp.status_code != 200:
        raise Exception(f"OpenRouter API error ({resp.status_code}): {resp.text[:500]}")

    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")


def _parse_json(text: str) -> dict:
    json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    raise ValueError(f"Could not parse JSON: {text[:200]}")


def generate_post_text(plan: dict, analysis: dict) -> dict:
    """
    Generate caption and hashtags for a post.

    Args:
        plan: The approved content plan
        analysis: The reference analysis

    Returns:
        Dict with 'caption', 'hashtags', 'alt_caption'
    """
    theme = plan.get("theme", analysis.get("theme", "lifestyle"))
    original_caption = analysis.get("original_caption", "")
    slides = plan.get("assets", [])

    prompt = (
        f"Write an Instagram post caption for a lifestyle/fashion character account.\n\n"
        f"Theme: {theme}\n"
        f"Number of slides: {len(slides)}\n"
        f"Slide descriptions: {json.dumps([s.get('description', '') for s in slides])}\n"
    )

    if original_caption:
        prompt += f"\nOriginal reference caption for inspiration (do NOT copy): {original_caption}\n"

    prompt += (
        "\nRules:\n"
        "- Caption: 100-200 characters, engaging, casual tone\n"
        "- Exactly 5 hashtags, mix of popular and niche\n"
        "- Write in English\n"
        "- No emojis overuse (1-2 max)\n"
        "- Sound authentic, not like a bot\n\n"
        "Return JSON:\n"
        "{\n"
        '  "caption": "main caption text",\n'
        '  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],\n'
        '  "alt_caption": "alternative caption option"\n'
        "}\n\n"
        "Output ONLY JSON."
    )

    response_text = _call_llm([{"role": "user", "content": prompt}])
    result = _parse_json(response_text)

    print(f"[Publish] Post text: caption={result.get('caption', '')[:50]}...")
    return result


def generate_story_text(plan: dict) -> dict:
    """
    Generate overlay text and sticker ideas for stories.

    Args:
        plan: The approved story plan

    Returns:
        Dict with per-frame text suggestions
    """
    frames = plan.get("assets", [])
    theme = plan.get("theme", "lifestyle")
    narrative = plan.get("narrative", "")

    prompt = (
        f"Write Instagram Story text overlays for a lifestyle character account.\n\n"
        f"Theme: {theme}\n"
        f"Narrative: {narrative}\n"
        f"Number of frames: {len(frames)}\n"
        f"Frame descriptions: {json.dumps([f.get('description', '') for f in frames])}\n\n"
        "For each frame, suggest:\n"
        "- Short overlay text (optional, not every frame needs it)\n"
        "- Sticker/poll idea (optional)\n\n"
        "Return JSON:\n"
        "{\n"
        '  "frames": [\n'
        "    {\n"
        '      "index": 0,\n'
        '      "overlay_text": "text or empty string",\n'
        '      "sticker_idea": "poll/question/emoji slider idea or empty string"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Output ONLY JSON."
    )

    response_text = _call_llm([{"role": "user", "content": prompt}])
    result = _parse_json(response_text)

    print(f"[Publish] Story text for {len(result.get('frames', []))} frames")
    return result


def generate_reel_text(plan: dict) -> dict:
    """
    Generate caption and hashtags for a reel.

    Args:
        plan: The approved reel plan

    Returns:
        Dict with 'caption', 'hashtags'
    """
    theme = plan.get("theme", "")
    motion_hint = plan.get("reference_analysis", {}).get("motion_hint", "")

    prompt = (
        f"Write an Instagram Reel caption for a lifestyle/fashion character account.\n\n"
        f"Theme: {theme}\n"
        f"Motion/action: {motion_hint}\n\n"
        "Rules:\n"
        "- Caption: short, catchy, 50-150 characters\n"
        "- Exactly 5 hashtags\n"
        "- Include 1 trending/viral hashtag if relevant\n"
        "- Write in English\n\n"
        "Return JSON:\n"
        "{\n"
        '  "caption": "reel caption",\n'
        '  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]\n'
        "}\n\n"
        "Output ONLY JSON."
    )

    response_text = _call_llm([{"role": "user", "content": prompt}])
    result = _parse_json(response_text)

    print(f"[Publish] Reel text: caption={result.get('caption', '')[:50]}...")
    return result
