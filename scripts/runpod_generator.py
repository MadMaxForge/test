#!/usr/bin/env python3
"""
RunPod Z-Image Turbo Generator — calls the ComfyUI endpoint to generate images.

Usage:
  python3 runpod_generator.py <username>           # generate from creative_prompts/<username>_prompts.json
  python3 runpod_generator.py --prompt "A w1man, ..." # generate a single image from a prompt
  python3 runpod_generator.py --test                # test endpoint with a simple prompt
"""

import argparse
import base64
import json
import os
import random
import sys
import time
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")

# RunPod endpoint config
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "4ijgr28bctaysk")
BASE_URL = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT}"

# ComfyUI Workflow template — only prompt (node 7) and seed (node 10) change
WORKFLOW = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "z_image_turbo_bf16.safetensors",
            "weight_dtype": "default"
        }
    },
    "2": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["1", 0],
            "lora_name": "REDZ15_DetailDaemonZ_lora_v1.1.safetensors",
            "strength_model": 0.50
        }
    },
    "3": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["2", 0],
            "lora_name": "Z-Breast-Slider.safetensors",
            "strength_model": 0.45
        }
    },
    "4": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["3", 0],
            "lora_name": "w1man.safetensors",
            "strength_model": 1.00
        }
    },
    "5": {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {
            "model": ["4", 0],
            "shift": 3
        }
    },
    "6": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "qwen_3_4b.safetensors",
            "type": "lumina2",
            "device": "default"
        }
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["6", 0],
            "text": "PROMPT_HERE"
        }
    },
    "8": {
        "class_type": "ConditioningZeroOut",
        "inputs": {
            "conditioning": ["7", 0]
        }
    },
    "9": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {
            "width": 1088,
            "height": 1920,
            "batch_size": 1
        }
    },
    "10": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["5", 0],
            "positive": ["7", 0],
            "negative": ["8", 0],
            "latent_image": ["9", 0],
            "seed": 0,
            "control_after_generate": "randomize",
            "steps": 10,
            "cfg": 1,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1
        }
    },
    "11": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "ae.safetensors"
        }
    },
    "12": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["10", 0],
            "vae": ["11", 0]
        }
    },
    "13": {
        "class_type": "SaveImage",
        "inputs": {
            "images": ["12", 0],
            "filename_prefix": "z-image"
        }
    }
}


def generate_image(prompt, poll_interval=5, max_wait=900):
    """
    Submit a generation job to RunPod and poll until complete.
    Returns PNG image bytes on success, raises Exception on failure.
    """
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["7"]["inputs"]["text"] = prompt
    workflow["10"]["inputs"]["seed"] = random.randint(1, 2**32)

    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}

    # 1. Submit job
    print(f"[RunPod] Submitting job...")
    resp = requests.post(
        f"{BASE_URL}/run",
        headers=headers,
        json={"input": {"workflow": workflow}},
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"RunPod submit failed ({resp.status_code}): {resp.text[:300]}")

    job_data = resp.json()
    job_id = job_data.get("id")
    if not job_id:
        raise Exception(f"No job ID in response: {job_data}")

    print(f"[RunPod] Job submitted: {job_id}")
    print(f"[RunPod] Status: {job_data.get('status', 'unknown')}")

    # 2. Poll for result
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for job {job_id}")

        time.sleep(poll_interval)

        status_resp = requests.get(
            f"{BASE_URL}/status/{job_id}",
            headers=headers,
            timeout=30,
        )

        if status_resp.status_code != 200:
            print(f"[RunPod] Status check failed ({status_resp.status_code}), retrying...")
            continue

        data = status_resp.json()
        status = data.get("status", "unknown")

        if status == "COMPLETED":
            print(f"[RunPod] Job completed in {elapsed:.0f}s")
            output = data.get("output", {})
            images = output.get("images", [])
            if not images:
                raise Exception(f"No images in output: {output}")
            img_b64 = images[0].get("data", "")
            if not img_b64:
                raise Exception("Empty image data in response")
            return base64.b64decode(img_b64)

        elif status == "FAILED":
            error = data.get("error", "Unknown error")
            raise Exception(f"Job failed: {error}")

        elif status in ("IN_QUEUE", "IN_PROGRESS"):
            print(f"[RunPod] {status} ({elapsed:.0f}s elapsed)...")

        else:
            print(f"[RunPod] Unknown status: {status} ({elapsed:.0f}s elapsed)")


def test_endpoint():
    """Test the RunPod endpoint with a simple prompt."""
    print("[Test] Testing RunPod Z-Image Turbo endpoint...")
    print(f"[Test] Endpoint: {BASE_URL}")
    print(f"[Test] API Key: {RUNPOD_API_KEY[:12]}...")

    test_prompt = (
        "A w1man, standing in a sunlit modern apartment, "
        "wearing a casual white t-shirt and jeans, natural lighting from large windows, "
        "soft shadows, relaxed pose leaning against a kitchen counter, "
        "warm golden hour light, photorealistic, high detail, 8k quality"
    )
    print(f"[Test] Prompt: {test_prompt[:100]}...")

    try:
        img_bytes = generate_image(test_prompt)
        output_dir = os.path.join(WORKSPACE, "output", "photos")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "test_generation.png")
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        size_kb = len(img_bytes) / 1024
        print(f"[Test] SUCCESS! Image saved: {output_path} ({size_kb:.0f} KB)")
        return True
    except Exception as e:
        print(f"[Test] FAILED: {e}")
        return False


def generate_from_prompts(username, limit=None):
    """Generate images from creative prompts file for a given username."""
    prompts_path = os.path.join(WORKSPACE, "creative_prompts", f"{username}_prompts.json")
    if not os.path.exists(prompts_path):
        print(f"[ERROR] Creative prompts not found: {prompts_path}")
        sys.exit(1)

    with open(prompts_path) as f:
        creative_data = json.load(f)

    prompts = creative_data.get("prompts", [])
    if limit:
        prompts = prompts[:limit]

    print(f"[RunPod] Generating {len(prompts)} images for @{username}...")

    output_dir = os.path.join(WORKSPACE, "output", "photos", username)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for i, p in enumerate(prompts):
        prompt_text = p.get("prompt", "")
        concept = p.get("concept", f"image_{i + 1}")
        print(f"\n[{i + 1}/{len(prompts)}] Generating: {concept}")
        print(f"  Prompt: {prompt_text[:100]}...")

        try:
            img_bytes = generate_image(prompt_text)
            safe_concept = concept.replace(" ", "_").replace("/", "_")[:50]
            filename = f"{username}_{safe_concept}_{i + 1}.png"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(img_bytes)

            size_kb = len(img_bytes) / 1024
            print(f"  [OK] Saved: {filepath} ({size_kb:.0f} KB)")
            results.append({
                "concept": concept,
                "prompt": prompt_text,
                "file": filepath,
                "size_bytes": len(img_bytes),
                "status": "success",
            })
        except Exception as e:
            print(f"  [FAIL] {e}")
            results.append({
                "concept": concept,
                "prompt": prompt_text,
                "file": None,
                "status": "failed",
                "error": str(e),
            })

    # Save generation log
    log = {
        "username": username,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_requested": len(prompts),
        "total_success": sum(1 for r in results if r["status"] == "success"),
        "total_failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    log_path = os.path.join(output_dir, "generation_log.json")
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"\n[RunPod] Generation complete: {log['total_success']}/{log['total_requested']} successful")
    print(f"[RunPod] Log saved: {log_path}")

    return log


def main():
    parser = argparse.ArgumentParser(description="RunPod Z-Image Turbo Generator")
    parser.add_argument("username", nargs="?", help="Generate from creative_prompts/<username>_prompts.json")
    parser.add_argument("--prompt", help="Generate a single image from a prompt")
    parser.add_argument("--test", action="store_true", help="Test endpoint with a simple prompt")
    parser.add_argument("--limit", type=int, help="Limit number of images to generate")

    args = parser.parse_args()

    if args.test:
        ok = test_endpoint()
        sys.exit(0 if ok else 1)

    if args.prompt:
        if not args.prompt.startswith("A w1man"):
            print("[WARN] Prompt should start with 'A w1man, ' for LoRA activation")
            args.prompt = "A w1man, " + args.prompt
        try:
            img_bytes = generate_image(args.prompt)
            output_dir = os.path.join(WORKSPACE, "output", "photos")
            os.makedirs(output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"single_{ts}.png")
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            print(f"[OK] Image saved: {output_path} ({len(img_bytes) / 1024:.0f} KB)")
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.username:
        generate_from_prompts(args.username, limit=args.limit)
        sys.exit(0)

    parser.error("Provide a username, --prompt, or --test")


if __name__ == "__main__":
    main()
