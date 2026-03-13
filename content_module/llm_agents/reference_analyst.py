"""
Reference Analyst — LLM-powered analysis of downloaded reference content.

For posts: describes structure, slides, poses, lighting, composition.
For reels: minimal analysis — camera distance, orientation, motion hint.

Uses vision-capable models via OpenRouter.
"""

import base64
import json
import re
import requests
from pathlib import Path
from typing import Optional

from content_module.core.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    MODEL_ANALYST,
)


def _image_to_base64(filepath: str) -> str:
    data = Path(filepath).read_bytes()
    return base64.b64encode(data).decode("utf-8")


def _call_llm(messages: list[dict], temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """Call OpenRouter LLM and return the response text."""
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY not set")

    resp = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL_ANALYST,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise Exception(f"OpenRouter API error ({resp.status_code}): {resp.text[:500]}")

    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return content


def _parse_json_from_text(text: str) -> dict:
    """Extract JSON object from LLM response text."""
    # Try to find JSON block
    json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # Try to find raw JSON object
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())

    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def analyze_post_reference(manifest: dict) -> dict:
    """
    Analyze a post/carousel reference.

    Examines each slide and produces a structured analysis:
    - Total slides, order, types
    - Per-slide: has_character, description, pose, lighting, composition
    - Overall theme, mood, style

    Args:
        manifest: Download manifest with file paths and metadata

    Returns:
        Analysis dict
    """
    files = [f for f in manifest.get("files", []) if f["type"] == "image"]
    if not files:
        return {
            "error": "No images found in manifest",
            "total_slides": 0,
            "slides": [],
        }

    # Build vision messages with all slide images
    image_content = []
    for f in files:
        filepath = f["path"]
        if Path(filepath).exists():
            b64 = _image_to_base64(filepath)
            ext = Path(filepath).suffix.lower()
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            image_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })

    if not image_content:
        return {"error": "Could not read any image files", "total_slides": 0, "slides": []}

    prompt = (
        "Analyze this Instagram post/carousel for content recreation. "
        f"There are {len(image_content)} slide(s). For each slide, describe:\n\n"
        "Return JSON with this structure:\n"
        "{\n"
        '  "total_slides": N,\n'
        '  "theme": "overall theme/mood of the post",\n'
        '  "style": "photography style (candid/professional/selfie/etc)",\n'
        '  "color_palette": "dominant colors",\n'
        '  "slides": [\n'
        "    {\n"
        '      "index": 0,\n'
        '      "has_character": true/false,\n'
        '      "description": "detailed description of the slide",\n'
        '      "pose": "character pose if present (standing/sitting/walking/etc)",\n'
        '      "camera_angle": "eye-level/above/below/side",\n'
        '      "lighting": "natural/studio/dramatic/soft/golden-hour",\n'
        '      "background": "description of background",\n'
        '      "outfit": "clothing description if character present",\n'
        '      "emotion": "facial expression/mood if character present",\n'
        '      "content_category": "character/world/detail/product/food/interior"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Output ONLY valid JSON. No markdown, no explanations."
    )

    messages = [
        {"role": "user", "content": image_content + [{"type": "text", "text": prompt}]}
    ]

    response_text = _call_llm(messages)
    analysis = _parse_json_from_text(response_text)

    # Add caption from metadata if available
    caption = manifest.get("metadata", {}).get("caption", "")
    if caption:
        analysis["original_caption"] = caption

    print(f"[Analyst] Post analysis: {analysis.get('total_slides', 0)} slides, "
          f"theme={analysis.get('theme', 'unknown')}")
    return analysis


def analyze_reel_reference(manifest: dict) -> dict:
    """
    Analyze a reel reference for motion control.

    Minimal analysis: camera distance, orientation, pose, motion hint.
    Uses first frame of the video (or thumbnail).

    Args:
        manifest: Download manifest with file paths

    Returns:
        Analysis dict for reel planning
    """
    # Find video thumbnail or first frame
    image_file = None
    video_file = None

    for f in manifest.get("files", []):
        if f["type"] == "thumbnail" and Path(f["path"]).exists():
            image_file = f["path"]
        elif f["type"] == "video" and Path(f["path"]).exists():
            video_file = f["path"]
        elif f["type"] == "image" and Path(f["path"]).exists():
            image_file = f["path"]

    # If we have a video but no thumbnail, extract first frame
    if video_file and not image_file:
        image_file = _extract_first_frame(video_file)

    if not image_file:
        return {
            "camera_distance": "medium",
            "orientation": "facing_camera",
            "pose": "standing",
            "z_image_prompt_hint": "medium shot, facing camera, natural pose",
            "analyzed": False,
            "note": "No image available for analysis",
        }

    b64 = _image_to_base64(image_file)
    ext = Path(image_file).suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    prompt = (
        "Analyze this video frame for motion control reference. Answer in JSON only:\n"
        "{\n"
        '  "camera_distance": "close-up / medium / full_body",\n'
        '  "orientation": "facing_camera / side_left / side_right / back",\n'
        '  "pose": "standing / sitting / walking / dancing / leaning / other",\n'
        '  "background_type": "indoor / outdoor / studio / other",\n'
        '  "lighting": "natural / studio / dramatic / soft",\n'
        '  "motion_hint": "brief description of likely motion in this scene",\n'
        '  "z_image_prompt_hint": "camera and pose description for generating a matching first frame"\n'
        "}\n"
        "Output ONLY JSON. No markdown."
    )

    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}
    ]

    try:
        response_text = _call_llm(messages, max_tokens=500)
        analysis = _parse_json_from_text(response_text)
        analysis["analyzed"] = True
        print(f"[Analyst] Reel analysis: distance={analysis.get('camera_distance')}, "
              f"pose={analysis.get('pose')}")
        return analysis
    except Exception as e:
        print(f"[Analyst] Reel analysis failed: {e}")
        return {
            "camera_distance": "medium",
            "orientation": "facing_camera",
            "pose": "standing",
            "z_image_prompt_hint": "medium shot, facing camera, natural pose",
            "analyzed": False,
            "error": str(e),
        }


def _extract_first_frame(video_path: str) -> Optional[str]:
    """Extract first frame from video using ffmpeg."""
    import subprocess
    output_path = str(Path(video_path).with_suffix(".frame.jpg"))
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-frames:v", "1",
             "-q:v", "2", output_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and Path(output_path).exists():
            print(f"[Analyst] Extracted first frame from video")
            return output_path
    except Exception as e:
        print(f"[Analyst] Could not extract frame: {e}")
    return None
