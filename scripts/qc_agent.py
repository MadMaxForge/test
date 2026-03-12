#!/usr/bin/env python3
"""
QC Agent - quality checks generated images using vision model.
Scores each image 0-10, threshold >= 7 to pass.
Supports retry logic with new seed (up to 3 attempts).

Usage: python3 qc_agent.py <username> [--threshold N] [--max-retries N]
"""

import json
import os
import re
import sys
import base64
import requests
import random
import time
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "")
RUNPOD_BASE_URL = "https://api.runpod.ai/v2/" + RUNPOD_ENDPOINT

QC_SYSTEM_PROMPT = """You are QC - a strict AI quality control agent for AI-generated Instagram images.
You receive a generated image and the prompt that was used to create it.

Your job is to evaluate the image on these 5 criteria. Be STRICT and look carefully:

1. Prompt adherence (0-10): Does the image match the described scene, outfit, setting, pose, camera angle?
2. Character consistency (0-10): Does the character look natural? Correct hair, face, body proportions? No uncanny valley?
3. Technical quality (0-10): This is the MOST CRITICAL check. Look very carefully for:
   - Extra or missing limbs (3 arms, 6 fingers, missing hand, etc.) = score 0-3
   - Mirror/reflection inconsistencies (reflection doesn't match the person) = score 0-4
   - Distorted hands, fingers fused together, extra fingers = score 0-4
   - Face distortions, asymmetric eyes, melted features = score 0-4
   - Background glitches, floating objects, impossible geometry = score 0-5
   - Blurry patches in subject (not background bokeh) = score 0-5
   - Text/watermark artifacts = score 0-5
   If ANY of the above are present, max score for this criterion is 5.
4. Composition (0-10): Good framing for vertical Instagram? Subject centered? Background appropriate?
5. Content safety (0-10): No nudity, no inappropriate content, Instagram-safe?

IMPORTANT ARTIFACT CHECKS (look at these BEFORE scoring):
- Count the number of arms visible. If more than 2 arms -> FAIL technical quality
- Count the number of hands visible. If more than 2 hands -> FAIL technical quality
- Count fingers on each visible hand. If any hand has more/fewer than 5 fingers -> reduce score
- If there is a mirror/reflection, check that the reflection matches the actual person (same pose, same clothing, correct mirror physics)
- Check hair consistency: no hair appearing/disappearing between main image and reflection

Calculate OVERALL score = average of all 5 criteria.

Output ONLY a valid JSON object:
{
  "scores": {
    "prompt_adherence": 0,
    "character_consistency": 0,
    "technical_quality": 0,
    "composition": 0,
    "content_safety": 0,
    "overall": 0.0
  },
  "pass": true,
  "artifact_check": {
    "arms_count": 2,
    "hands_count": 2,
    "finger_issues": false,
    "mirror_consistent": true,
    "extra_limbs": false
  },
  "issues": ["issue1 if any"],
  "notes": "brief assessment"
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences.
Be STRICT. It is better to fail a good image than to pass a bad one."""


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

    print("[ERROR] Could not parse JSON from QC response")
    return None


def evaluate_image(image_path, prompt_text):
    """Send image to vision model for QC evaluation."""
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        return None

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    ext = Path(image_path).suffix.lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    content_parts = [
        {"type": "text", "text": (
            "Evaluate this AI-generated image for Instagram quality.\n\n"
            "The prompt used to generate this image was:\n"
            "---\n" + prompt_text + "\n---\n\n"
            "Score each criterion 0-10 and determine if overall >= 7 (pass).\n"
            "Output ONLY the JSON evaluation object."
        )},
        {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + img_b64}}
    ]

    messages = [
        {"role": "system", "content": QC_SYSTEM_PROMPT},
        {"role": "user", "content": content_parts},
    ]

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 16000,
        },
        timeout=180,
    )

    if resp.status_code != 200:
        print("[ERROR] QC API returned %d" % resp.status_code)
        return None

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        print("[ERROR] No choices in QC API response: %s" % str(data)[:500])
        return None
    content = choices[0].get("message", {}).get("content")
    if not content:
        print("[ERROR] Empty content in QC response. Full response: %s" % str(data)[:500])
        return None
    return parse_json_response(content)


def regenerate_image(prompt_text):
    """Regenerate an image via RunPod with a new random seed."""
    WORKFLOW = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "REDZ15_DetailDaemonZ_lora_v1.1.safetensors", "strength_model": 0.50}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": "Z-Breast-Slider.safetensors", "strength_model": 0.45}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["3", 0], "lora_name": "w1man.safetensors", "strength_model": 1.00}},
        "5": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["4", 0], "shift": 3}},
        "6": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["6", 0], "text": prompt_text}},
        "8": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
        "9": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1088, "height": 1920, "batch_size": 1}},
        "10": {"class_type": "KSampler", "inputs": {
            "model": ["5", 0], "positive": ["7", 0], "negative": ["8", 0],
            "latent_image": ["9", 0], "seed": random.randint(1, 2**32),
            "control_after_generate": "randomize", "steps": 10, "cfg": 1,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1
        }},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["11", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "z-image"}}
    }

    resp = requests.post(
        RUNPOD_BASE_URL + "/run",
        headers={"Authorization": "Bearer " + RUNPOD_API_KEY},
        json={"input": {"workflow": WORKFLOW}},
        timeout=30
    )
    if resp.status_code != 200:
        print("[ERROR] RunPod submit failed: %d" % resp.status_code)
        return None

    job_id = resp.json().get("id")
    if not job_id:
        print("[ERROR] No job ID from RunPod")
        return None

    print("    [RunPod] Job %s - polling..." % job_id)
    start = time.time()
    while time.time() - start < 600:
        time.sleep(5)
        elapsed = int(time.time() - start)
        sr = requests.get(
            RUNPOD_BASE_URL + "/status/" + job_id,
            headers={"Authorization": "Bearer " + RUNPOD_API_KEY},
            timeout=30
        )
        sdata = sr.json()
        status = sdata.get("status", "UNKNOWN")
        if status == "COMPLETED":
            output = sdata.get("output", {})
            imgs = output.get("images", []) if isinstance(output, dict) else []
            if imgs:
                return base64.b64decode(imgs[0]["data"])
            return None
        elif status == "FAILED":
            err = sdata.get("error", "unknown")
            print("    [RunPod] FAILED: %s" % str(err))
            return None
        if elapsed % 30 == 0:
            print("    [RunPod] %ds - %s" % (elapsed, status))
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 qc_agent.py <username> [--threshold N] [--max-retries N]")
        sys.exit(1)

    username = sys.argv[1]
    threshold = 7
    max_retries = 3

    if "--threshold" in sys.argv:
        idx = sys.argv.index("--threshold")
        threshold = int(sys.argv[idx + 1])
    if "--max-retries" in sys.argv:
        idx = sys.argv.index("--max-retries")
        max_retries = int(sys.argv[idx + 1])

    print("[QC] Quality checking images for @%s (threshold=%d, max_retries=%d)" % (username, threshold, max_retries))

    # Load memory for learned artifact patterns
    mem = None
    qc_memory_context = ""
    try:
        from agent_memory import AgentMemory
        mem = AgentMemory()
        qc_memory_context = mem.build_qc_context(max_examples=5)
        if qc_memory_context:
            print("[QC] Loaded learned artifact patterns from memory")
    except Exception as e:
        print("[QC] Warning: Could not load memory: %s" % e)

    # Load creative prompts for reference
    prompts_path = os.path.join(WORKSPACE, "creative_prompts", username + "_prompts.json")
    prompts_data = {}
    if os.path.exists(prompts_path):
        with open(prompts_path) as f:
            prompts_data = json.load(f)

    # Find generated images
    photos_dir = os.path.join(WORKSPACE, "output", "photos", username)
    if not os.path.exists(photos_dir):
        print("[ERROR] No photos directory: %s" % photos_dir)
        sys.exit(1)

    image_files = sorted(Path(photos_dir).glob("*.png"))
    if not image_files:
        image_files = sorted(Path(photos_dir).glob("*.jpg"))

    if not image_files:
        print("[ERROR] No images found in %s" % photos_dir)
        sys.exit(1)

    print("[QC] Found %d images to check" % len(image_files))

    prompts_list = prompts_data.get("prompts", [])

    # Build a map from image filename pattern to prompt for correct matching
    prompt_by_index = {}
    for idx, p in enumerate(prompts_list):
        prompt_by_index[idx + 1] = p.get("prompt", "")

    qc_results = []

    for i, img_path in enumerate(image_files):
        # Match prompt by extracting index from filename (e.g. username_SceneName_2.png -> index 2)
        prompt_text = ""
        fname = img_path.stem  # e.g. kyliejenner_Fashion_Archive_2
        parts = fname.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            img_idx = int(parts[1])
            prompt_text = prompt_by_index.get(img_idx, "")
        if not prompt_text and i < len(prompts_list):
            prompt_text = prompts_list[i].get("prompt", "")

        print("\n[QC] Image %d/%d: %s" % (i + 1, len(image_files), img_path.name))

        best_result = None
        best_score = 0

        for attempt in range(max_retries):
            if attempt > 0:
                print("  [QC] Retry %d/%d - regenerating with new seed..." % (attempt, max_retries - 1))
                new_bytes = regenerate_image(prompt_text)
                if new_bytes:
                    with open(str(img_path), "wb") as f:
                        f.write(new_bytes)
                    print("  [QC] Regenerated image saved")
                else:
                    print("  [QC] Regeneration failed, keeping current image")

            print("  [QC] Evaluating (attempt %d)..." % (attempt + 1))
            result = evaluate_image(str(img_path), prompt_text)

            if result is None:
                print("  [QC] Evaluation failed")
                continue

            scores = result.get("scores", {})
            overall = scores.get("overall", 0)
            print("  [QC] Score: %.1f/10 (%s)" % (overall, "PASS" if overall >= threshold else "FAIL"))

            if overall > best_score:
                best_score = overall
                best_result = result

            if overall >= threshold:
                break

        if best_result is None:
            best_result = {"scores": {"overall": 0}, "pass": False, "issues": ["evaluation failed"], "notes": "Could not evaluate"}

        best_result["image"] = img_path.name
        best_result["attempts"] = min(attempt + 1, max_retries)
        best_result["final_pass"] = best_score >= threshold
        qc_results.append(best_result)

    # Summary
    passed = sum(1 for r in qc_results if r.get("final_pass", False))
    total = len(qc_results)
    avg_score = sum(r.get("scores", {}).get("overall", 0) for r in qc_results) / max(total, 1)

    qc_report = {
        "username": username,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "threshold": threshold,
        "total_images": total,
        "passed": passed,
        "failed": total - passed,
        "average_score": round(avg_score, 1),
        "results": qc_results
    }

    # Save QC results to memory
    try:
        if mem is None:
            from agent_memory import AgentMemory
            mem = AgentMemory()
        for r in qc_results:
            score = r.get("scores", {}).get("overall", 0)
            issues = r.get("issues", [])
            # Log artifact patterns for learning
            if issues:
                mem.log_event("qc", "artifacts_detected", {
                    "image": r.get("image", ""),
                    "score": score,
                    "issues": issues,
                    "notes": r.get("notes", ""),
                }, lesson="Image %s scored %.1f - issues: %s" % (
                    r.get("image", "?"), score, ", ".join(issues[:3])))
        mem.log_event("qc", "batch_complete", {
            "username": username,
            "total": total,
            "passed": passed,
            "avg_score": round(avg_score, 1),
        }, lesson="QC batch for @%s: %d/%d passed, avg %.1f" % (
            username, passed, total, avg_score))
        mem.close()
        print("[QC] Results saved to memory database")
    except Exception as e:
        print("[QC] Warning: Could not save to memory: %s" % e)

    # Save QC report
    output_dir = os.path.join(WORKSPACE, "qc_reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, username + "_qc.json")

    with open(output_path, "w") as f:
        json.dump(qc_report, f, indent=2, ensure_ascii=False)

    print("\n[QC] === REPORT ===")
    print("[QC] Total: %d | Passed: %d | Failed: %d | Avg Score: %.1f" % (total, passed, total - passed, avg_score))
    print("[QC] Report saved: %s" % output_path)


if __name__ == "__main__":
    main()
