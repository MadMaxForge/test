"""
Planner — LLM-powered content planning.

Takes analysis output and builds a structured plan for ONE content type:
  - post_plan: how many slides, which are character, which are world
  - story_plan: how many frames, what type each frame is
  - reel_plan: start frame spec + motion reference

Does NOT write prompts — that's Creative's job.
Does NOT choose generators — that's Asset Router's job (but Planner suggests has_character).
"""

import json
import re
import requests

from content_module.core.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    MODEL_PLANNER,
    MAX_POST_SLIDES,
    MAX_STORY_FRAMES,
    CHARACTER_RATIO_TARGET,
)


def _call_llm(messages: list[dict], temperature: float = 0.4, max_tokens: int = 2000) -> str:
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY not set")

    resp = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL_PLANNER,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
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
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def plan_post(analysis: dict) -> dict:
    """
    Build a post plan from reference analysis.

    Args:
        analysis: Output of reference_analyst.analyze_post_reference()

    Returns:
        Plan dict with assets list
    """
    total_ref_slides = analysis.get("total_slides", 1)
    theme = analysis.get("theme", "lifestyle")
    style = analysis.get("style", "candid")
    slides_info = analysis.get("slides", [])

    prompt = (
        f"You are planning an Instagram carousel post for a fictional character account.\n\n"
        f"The reference post has {total_ref_slides} slides.\n"
        f"Theme: {theme}\n"
        f"Style: {style}\n\n"
        f"Reference slides:\n{json.dumps(slides_info, indent=2)}\n\n"
        f"Rules:\n"
        f"- Maximum {MAX_POST_SLIDES} slides\n"
        f"- Target ~{int(CHARACTER_RATIO_TARGET*100)}% character slides, rest world/detail\n"
        f"- Character slides show our girl (will use Z-Image with LoRA)\n"
        f"- World slides show environment/objects/details (will use Nano Banana)\n"
        f"- First slide should typically feature the character\n"
        f"- Format: 4:5 aspect ratio for all slides\n"
        f"- Adapt the reference structure to our character, don't copy literally\n\n"
        f"Return JSON:\n"
        '{{\n'
        '  "content_type": "post",\n'
        '  "total_slides": N,\n'
        '  "theme": "adapted theme",\n'
        '  "mood": "overall mood",\n'
        '  "assets": [\n'
        '    {{\n'
        '      "index": 0,\n'
        '      "role": "slide_1",\n'
        '      "has_character": true/false,\n'
        '      "description": "what this slide should show",\n'
        '      "reference_notes": "what to take from the reference"\n'
        '    }}\n'
        '  ]\n'
        '}}\n\n'
        "Output ONLY valid JSON."
    )

    response_text = _call_llm([{"role": "user", "content": prompt}])
    plan = _parse_json(response_text)
    plan["content_type"] = "post"

    # Enforce limits
    if len(plan.get("assets", [])) > MAX_POST_SLIDES:
        plan["assets"] = plan["assets"][:MAX_POST_SLIDES]
    plan["total_slides"] = len(plan.get("assets", []))

    print(f"[Planner] Post plan: {plan['total_slides']} slides, "
          f"theme={plan.get('theme', '?')}")
    return plan


def plan_story(analysis: dict, theme: str = "") -> dict:
    """
    Build a story plan.

    Stories are independent from posts. They can be based on:
    - A reference
    - A theme/topic
    - A mood

    Args:
        analysis: Optional analysis dict (can be empty for theme-based stories)
        theme: Optional theme string

    Returns:
        Plan dict with assets list
    """
    context = ""
    if analysis and analysis.get("slides"):
        context = f"Reference analysis:\n{json.dumps(analysis, indent=2)}\n\n"
    if theme:
        context += f"Requested theme: {theme}\n\n"

    prompt = (
        f"You are planning Instagram Stories for a fictional character account.\n\n"
        f"{context}"
        f"Rules:\n"
        f"- Maximum {MAX_STORY_FRAMES} story frames\n"
        f"- Format: 9:16 vertical\n"
        f"- Stories are NOT just cropped posts — they are their own format\n"
        f"- Mix character frames with world/atmosphere frames\n"
        f"- Can include: character shots, environment, details, text-overlay backgrounds\n"
        f"- Should feel like a cohesive mini-narrative\n\n"
        f"Return JSON:\n"
        '{{\n'
        '  "content_type": "story",\n'
        '  "total_frames": N,\n'
        '  "theme": "story theme",\n'
        '  "narrative": "brief story arc description",\n'
        '  "assets": [\n'
        '    {{\n'
        '      "index": 0,\n'
        '      "role": "story_1",\n'
        '      "has_character": true/false,\n'
        '      "description": "what this frame should show",\n'
        '      "overlay_text_idea": "optional text overlay suggestion"\n'
        '    }}\n'
        '  ]\n'
        '}}\n\n'
        "Output ONLY valid JSON."
    )

    response_text = _call_llm([{"role": "user", "content": prompt}])
    plan = _parse_json(response_text)
    plan["content_type"] = "story"

    if len(plan.get("assets", [])) > MAX_STORY_FRAMES:
        plan["assets"] = plan["assets"][:MAX_STORY_FRAMES]
    plan["total_frames"] = len(plan.get("assets", []))

    print(f"[Planner] Story plan: {plan['total_frames']} frames, "
          f"theme={plan.get('theme', '?')}")
    return plan


def plan_reel(analysis: dict) -> dict:
    """
    Build a reel plan from reference analysis.

    Reel plan is simpler: just start frame + motion reference.
    The actual video generation happens via Kling after start frame approval.

    Args:
        analysis: Output of reference_analyst.analyze_reel_reference()

    Returns:
        Plan dict with assets list
    """
    camera_distance = analysis.get("camera_distance", "medium")
    orientation = analysis.get("orientation", "facing_camera")
    pose = analysis.get("pose", "standing")
    motion_hint = analysis.get("motion_hint", "natural movement")
    z_hint = analysis.get("z_image_prompt_hint", "")

    plan = {
        "content_type": "reel",
        "theme": f"Motion transfer — {motion_hint}",
        "reference_analysis": analysis,
        "assets": [
            {
                "index": 0,
                "role": "start_frame",
                "has_character": True,
                "generator": "z_image",
                "description": (
                    f"Start frame for reel. Character in {camera_distance} shot, "
                    f"{orientation}, {pose}. {z_hint}"
                ),
            },
            {
                "index": 1,
                "role": "motion_reference",
                "has_character": False,
                "generator": "user_provided",
                "description": "Motion reference video (from the original reel)",
            },
            {
                "index": 2,
                "role": "final_render",
                "has_character": True,
                "generator": "kling",
                "description": (
                    f"Final reel: motion from reference applied to start frame. "
                    f"Motion: {motion_hint}"
                ),
            },
        ],
    }

    print(f"[Planner] Reel plan: start_frame -> approve -> Kling render")
    return plan
