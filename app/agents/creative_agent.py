"""Creative Agent — generates prompts, sends to RunPod z-image, performs QC.

Responsibilities:
  1. Take content brief from Research Agent
  2. Generate detailed prompts (500+ chars) following brand guide
  3. Submit to RunPod z-image-turbo endpoint
  4. QC generated images using vision model
  5. Retry if quality is insufficient
"""

import asyncio
import base64
import json
import logging
import os
import random
from typing import Optional

import httpx

from app.agents import llm_client
from app.agents.brand_guide import (
    get_character_appearance,
    get_generation_params,
    get_prompt_tips,
    get_restrictions,
)
from app.agents.workflows.z_image_turbo import build_zimage_workflow, build_prompt_with_guide
from app.services.runpod_api import submit_comfyui_job, check_job_status

logger = logging.getLogger(__name__)


async def generate_prompt(
    scene_idea: str,
    brand_guide: dict,
    reference_descriptions: Optional[list[dict]] = None,
) -> str:
    """Generate a detailed z-image prompt from a scene idea.

    The prompt must be 500+ characters and follow the brand guide structure:
    trigger word + scene + background + outfit + pose + camera + lighting.

    Args:
        scene_idea: High-level scene concept (e.g. "cosplay in neon bedroom").
        brand_guide: Character brand guide.
        reference_descriptions: Optional reference photo descriptions for inspiration.

    Returns:
        Full detailed prompt string ready for z-image.
    """
    char = brand_guide.get("character", {})
    appearance = char.get("appearance", {})
    trigger = char.get("trigger_word", "w1man")
    tips = get_prompt_tips(brand_guide)
    restrictions = get_restrictions(brand_guide)
    style = brand_guide.get("style", {})

    ref_context = ""
    if reference_descriptions:
        ref_context = f"\nReference photo descriptions for inspiration:\n{json.dumps(reference_descriptions[:3], indent=2)}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert prompt engineer for z-image-turbo AI image generation. "
                "Your job is to write extremely detailed, vivid prompts that produce photorealistic images.\n\n"
                "RULES:\n"
                f"1. ALWAYS start with 'A {trigger},'\n"
                "2. Prompt MUST be at least 500 characters, ideally 600-1000\n"
                "3. Structure: scene setting -> background -> character appearance -> "
                "outfit (VERY detailed) -> pose/hands -> camera -> lighting\n"
                "4. Use descriptive, specific language (not vague)\n"
                "5. Include fabric textures, color shades, fit descriptions for outfits\n"
                "6. Specify camera angle, framing, and depth of field\n"
                "7. Describe lighting with color, direction, intensity, and shadow quality\n"
                "8. Add atmosphere words at the end\n\n"
                f"Character appearance:\n"
                f"- Hair: {appearance.get('hair', 'long black hair')}\n"
                f"- Eyes: {appearance.get('eyes', 'wide expressive eyes')}\n"
                f"- Face: {appearance.get('face', 'soft youthful face')}\n"
                f"- Body: {appearance.get('body_type', 'fit')}\n"
                f"- Skin: {appearance.get('skin', 'smooth natural skin')}\n"
                f"- Makeup: {appearance.get('makeup', 'light natural')}\n\n"
                f"Style preferences: {json.dumps(style.get('favorite_themes', []))}\n"
                f"Color palette: {json.dumps(style.get('color_palette', []))}\n"
                f"Restrictions: {'; '.join(restrictions)}\n\n"
                f"Prompt tips: {json.dumps(tips)}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Scene idea: {scene_idea}\n"
                f"{ref_context}\n"
                "Write ONE complete prompt. Output ONLY the prompt text, nothing else. "
                "No quotes, no labels, no explanation. Just the raw prompt. "
                "Make it at least 500 characters."
            ),
        },
    ]

    prompt_text = await llm_client.chat_completion(
        messages=messages,
        temperature=0.8,
        max_tokens=2048,
    )

    # Clean up - remove any wrapping quotes or labels
    prompt_text = prompt_text.strip().strip('"').strip("'")
    if prompt_text.lower().startswith("prompt:"):
        prompt_text = prompt_text[7:].strip()

    # Ensure trigger word is present
    prompt_text = build_prompt_with_guide(prompt_text, brand_guide)

    return prompt_text


async def generate_image(
    prompt: str,
    brand_guide: dict,
    seed: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> dict:
    """Submit a prompt to RunPod z-image-turbo and wait for result.

    Args:
        prompt: Full detailed prompt.
        brand_guide: Character brand guide for generation params.
        seed: Optional seed for reproducibility.
        width: Override width.
        height: Override height.

    Returns:
        dict with 'runpod_job_id', 'status', 'images' (list of image data).
    """
    gen_params = get_generation_params(brand_guide)

    w = width or gen_params.get("default_width", 1088)
    h = height or gen_params.get("default_height", 1920)

    # Get character LoRA from guide
    lora_chain = gen_params.get("lora_chain", [])
    char_lora = "w1man.safetensors"
    char_strength = 1.0
    detail_strength = 0.5
    slider_strength = 0.45

    for lora in lora_chain:
        name = lora.get("name", "")
        strength = lora.get("strength", 1.0)
        if "w1man" in name.lower() or "character" in name.lower():
            char_lora = name
            char_strength = strength
        elif "detail" in name.lower():
            detail_strength = strength
        elif "slider" in name.lower() or "breast" in name.lower():
            slider_strength = strength

    workflow = build_zimage_workflow(
        prompt=prompt,
        width=w,
        height=h,
        seed=seed,
        lora_name=char_lora,
        lora_strength=char_strength,
        detail_lora_strength=detail_strength,
        slider_lora_strength=slider_strength,
    )

    # Submit to RunPod
    result = await submit_comfyui_job(workflow=workflow)
    job_id = result.get("id", "")

    logger.info(f"Submitted z-image job: {job_id}")

    return {
        "runpod_job_id": job_id,
        "status": result.get("status", "UNKNOWN"),
        "prompt": prompt,
        "seed": seed,
        "width": w,
        "height": h,
    }


async def wait_for_image(
    runpod_job_id: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict:
    """Poll RunPod until the z-image job completes.

    Args:
        runpod_job_id: The RunPod job ID.
        timeout_seconds: Max wait time.
        poll_interval: Seconds between polls.

    Returns:
        dict with 'status', 'output' (images), 'error'.
    """
    elapsed = 0
    while elapsed < timeout_seconds:
        result = await check_job_status(runpod_job_id)
        status = result.get("status", "")

        if status == "COMPLETED":
            return {
                "status": "completed",
                "output": result.get("output", {}),
                "runpod_job_id": runpod_job_id,
            }
        elif status == "FAILED":
            return {
                "status": "failed",
                "error": result.get("error", "Unknown error"),
                "runpod_job_id": runpod_job_id,
            }

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return {
        "status": "timeout",
        "error": f"Job timed out after {timeout_seconds}s",
        "runpod_job_id": runpod_job_id,
    }


async def quality_check(
    image_base64: str,
    original_prompt: str,
    brand_guide: dict,
) -> dict:
    """QC a generated image using vision model.

    Checks:
    - Does the image match the prompt?
    - Is the character consistent (hair, face)?
    - Are there artifacts or distortions?
    - Does it follow content restrictions?

    Args:
        image_base64: Base64-encoded generated image.
        original_prompt: The prompt used to generate the image.
        brand_guide: Character brand guide.

    Returns:
        dict with 'score' (1-10), 'passed' (bool), 'issues' (list), 'feedback'.
    """
    restrictions = get_restrictions(brand_guide)
    char = brand_guide.get("character", {})
    appearance = char.get("appearance", {})

    instruction = (
        "You are a quality control agent for AI-generated Instagram images. "
        "Rate this image on a scale of 1-10 and identify any issues.\n\n"
        f"The image was generated from this prompt:\n{original_prompt[:500]}\n\n"
        f"Expected character appearance:\n"
        f"- Hair: {appearance.get('hair')}\n"
        f"- Face: {appearance.get('face')}\n"
        f"- Eyes: {appearance.get('eyes')}\n\n"
        f"Content restrictions: {'; '.join(restrictions)}\n\n"
        "Check for:\n"
        "1. Prompt accuracy: Does the image match the described scene?\n"
        "2. Character consistency: Hair color/style, face shape, expression\n"
        "3. Artifacts: Extra fingers, distorted face, blurry areas, text artifacts\n"
        "4. Composition: Good framing, not cropped weirdly\n"
        "5. Content policy: No restriction violations\n\n"
        "Respond in JSON format:\n"
        '{"score": 1-10, "passed": true/false (passed if score >= 7), '
        '"issues": ["list of specific issues"], '
        '"feedback": "brief overall assessment"}'
    )

    raw = await llm_client.describe_image(
        image_base64=image_base64,
        instruction=instruction,
    )

    # Parse JSON response
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {
            "score": 5,
            "passed": False,
            "issues": ["Could not parse QC response"],
            "feedback": raw,
        }

    # Ensure passed flag is consistent with score
    score = result.get("score", 5)
    result["passed"] = score >= 7

    return result


async def generate_carousel(
    content_brief: dict,
    brand_guide: dict,
    max_retries: int = 1,
) -> dict:
    """Generate a full carousel of images from a content brief.

    For each image plan in the brief:
    1. Generate/refine the prompt
    2. Submit to z-image
    3. Wait for result
    4. QC the image
    5. Retry if needed

    Args:
        content_brief: Output from Research Agent's create_content_brief().
        brand_guide: Character brand guide.
        max_retries: Max retries per image if QC fails.

    Returns:
        dict with 'images' (list of generated images), 'caption', 'hashtags'.
    """
    image_plans = content_brief.get("image_plans", [])
    if not image_plans:
        raise ValueError("No image plans in content brief")

    generated_images = []

    for i, plan in enumerate(image_plans):
        logger.info(f"Generating image {i + 1}/{len(image_plans)}: {plan.get('theme', 'unknown')}")

        prompt = plan.get("full_prompt", "")
        if not prompt or len(prompt) < 100:
            # Generate a proper prompt from the theme
            prompt = await generate_prompt(
                scene_idea=plan.get("theme", "casual indoor photo"),
                brand_guide=brand_guide,
            )

        # Ensure prompt has trigger word
        prompt = build_prompt_with_guide(prompt, brand_guide)

        attempts = 0
        best_result: Optional[dict] = None

        while attempts <= max_retries:
            # Generate
            seed = random.randint(1, 2**53)
            job_info = await generate_image(
                prompt=prompt,
                brand_guide=brand_guide,
                seed=seed,
            )

            # Wait for result
            result = await wait_for_image(job_info["runpod_job_id"])

            if result["status"] != "completed":
                logger.warning(f"Image generation failed: {result.get('error')}")
                attempts += 1
                continue

            # Extract image data for QC
            output = result.get("output", {})
            image_data = _extract_image_from_output(output)

            if image_data:
                # Run QC
                qc_result = await quality_check(
                    image_base64=image_data["base64"],
                    original_prompt=prompt,
                    brand_guide=brand_guide,
                )

                image_entry = {
                    "index": i,
                    "theme": plan.get("theme", ""),
                    "prompt": prompt,
                    "seed": seed,
                    "runpod_job_id": job_info["runpod_job_id"],
                    "qc_score": qc_result.get("score", 0),
                    "qc_passed": qc_result.get("passed", False),
                    "qc_feedback": qc_result.get("feedback", ""),
                    "image_url": image_data.get("url"),
                    "image_base64": image_data.get("base64"),
                }

                if qc_result.get("passed") or attempts >= max_retries:
                    best_result = image_entry
                    break
                else:
                    logger.info(f"QC failed (score {qc_result.get('score')}), retrying...")
                    if best_result is None or qc_result.get("score", 0) > best_result.get("qc_score", 0):
                        best_result = image_entry
            else:
                best_result = {
                    "index": i,
                    "theme": plan.get("theme", ""),
                    "prompt": prompt,
                    "seed": seed,
                    "runpod_job_id": job_info["runpod_job_id"],
                    "qc_score": 0,
                    "qc_passed": False,
                    "qc_feedback": "Could not extract image from output",
                    "image_url": None,
                    "image_base64": None,
                }

            attempts += 1

        if best_result:
            generated_images.append(best_result)

    return {
        "images": generated_images,
        "caption": content_brief.get("caption", ""),
        "hashtags": content_brief.get("hashtags", []),
        "carousel_order": content_brief.get("carousel_order", list(range(len(generated_images)))),
    }


def _extract_image_from_output(output: object) -> Optional[dict]:
    """Extract image data from RunPod worker output.

    Returns dict with 'base64' and optionally 'url', or None.
    """
    if not isinstance(output, dict):
        return None

    images_list = output.get("images", [])
    if isinstance(images_list, list):
        for item in images_list:
            if not isinstance(item, dict):
                continue
            data = item.get("data", "")
            item_type = item.get("type", "")

            if item_type == "base64" and data:
                return {"base64": data, "url": None}
            elif item_type == "s3_url" and data:
                return {"base64": "", "url": str(data)}

    # Try legacy format
    message = output.get("message")
    if isinstance(message, str) and message.startswith("http"):
        return {"base64": "", "url": message}

    return None
