#!/usr/bin/env python3
"""
Z-Image Turbo Generator — calls local ComfyUI via SSH tunnel to generate images.

Architecture: VPS -> SSH reverse tunnel -> Laptop (ComfyUI on RTX 4070)
The SSH tunnel maps localhost:8001 on VPS to localhost:8001 on the laptop.

Usage:
  python3 runpod_generator.py <username>           # generate from creative_prompts/<username>_prompts.json
  python3 runpod_generator.py --prompt "A w1man, ..." # generate a single image from a prompt
  python3 runpod_generator.py --test                # test endpoint with a simple prompt
"""

import argparse
import json
import os
import random
import sys
import time
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")

# ComfyUI endpoint config (via SSH reverse tunnel: laptop:8001 -> VPS localhost:8001)
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8001")

# Aspect ratio presets for Instagram content types
# Maps content_type -> (width, height)
ASPECT_RATIOS = {
    "feed": (1080, 1350),       # 4:5 — Instagram feed / carousel
    "story": (1088, 1920),      # 9:16 — Stories
    "reel": (1088, 1920),       # 9:16 — Reels
    "square": (1024, 1024),     # 1:1 — Square post
    "default": (1088, 1920),    # 9:16 — Default (vertical portrait)
}

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


def _check_comfyui():
    """Check if ComfyUI is reachable via the SSH tunnel."""
    try:
        resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=10)
        if resp.status_code == 200:
            stats = resp.json()
            devices = stats.get("devices", [])
            if devices:
                gpu = devices[0]
                vram_total_gb = gpu.get("vram_total", 0) / (1024 ** 3)
                vram_free_gb = gpu.get("vram_free", 0) / (1024 ** 3)
                print(f"[ComfyUI] Connected: {gpu.get('name', 'unknown')}")
                print(f"[ComfyUI] VRAM: {vram_free_gb:.1f}/{vram_total_gb:.1f} GB free")
            return True
        return False
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return False


def generate_image(prompt, content_type="default", poll_interval=5, max_wait=900):
    """
    Submit a workflow to ComfyUI and poll until the image is ready.
    Returns PNG image bytes on success, raises Exception on failure.

    Architecture: VPS sends workflow to localhost:8001 (SSH tunnel -> laptop ComfyUI).
    ComfyUI processes the workflow on the laptop GPU, saves the image, and we
    retrieve it via the /view endpoint.

    Args:
        prompt: Text prompt for image generation
        content_type: 'feed' (4:5), 'story' (9:16), 'reel' (9:16), 'square' (1:1), 'default' (9:16)
        poll_interval: Seconds between status checks
        max_wait: Maximum wait time in seconds
    """
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["7"]["inputs"]["text"] = prompt
    workflow["10"]["inputs"]["seed"] = random.randint(1, 2**32)

    # Set dimensions based on content type
    width, height = ASPECT_RATIOS.get(content_type, ASPECT_RATIOS["default"])
    workflow["9"]["inputs"]["width"] = width
    workflow["9"]["inputs"]["height"] = height
    print(f"[ComfyUI] Content type: {content_type} ({width}x{height})")

    # 1. Queue the workflow prompt
    print("[ComfyUI] Submitting workflow...")
    resp = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"ComfyUI submit failed ({resp.status_code}): {resp.text[:300]}")

    prompt_data = resp.json()
    prompt_id = prompt_data.get("prompt_id")
    if not prompt_id:
        raise Exception(f"No prompt_id in response: {prompt_data}")

    print(f"[ComfyUI] Workflow queued: {prompt_id}")

    # 2. Poll /history/{prompt_id} until the job is done
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for prompt {prompt_id}")

        time.sleep(poll_interval)

        try:
            history_resp = requests.get(
                f"{COMFYUI_URL}/history/{prompt_id}",
                timeout=30,
            )
        except requests.exceptions.ConnectionError:
            print(f"[ComfyUI] Connection lost, retrying... ({elapsed:.0f}s elapsed)")
            continue

        if history_resp.status_code != 200:
            print(f"[ComfyUI] History check failed ({history_resp.status_code}), retrying...")
            continue

        history = history_resp.json()

        if prompt_id not in history:
            # Job still in queue or processing
            try:
                queue_resp = requests.get(f"{COMFYUI_URL}/queue", timeout=10)
                if queue_resp.status_code == 200:
                    queue_data = queue_resp.json()
                    running = queue_data.get("queue_running", [])
                    pending = queue_data.get("queue_pending", [])
                    if running:
                        print(f"[ComfyUI] Processing... ({elapsed:.0f}s elapsed)")
                    elif pending:
                        print(f"[ComfyUI] In queue ({len(pending)} pending)... ({elapsed:.0f}s elapsed)")
                    else:
                        print(f"[ComfyUI] Waiting... ({elapsed:.0f}s elapsed)")
            except Exception:
                print(f"[ComfyUI] Waiting... ({elapsed:.0f}s elapsed)")
            continue

        # Job finished — extract output image info
        job_result = history[prompt_id]

        # Check for errors in status
        status_data = job_result.get("status", {})
        if status_data.get("status_str") == "error":
            messages = status_data.get("messages", [])
            error_msg = str(messages) if messages else "Unknown error"
            raise Exception(f"ComfyUI workflow failed: {error_msg}")

        outputs = job_result.get("outputs", {})

        # Find the SaveImage node output (node 13)
        save_node_output = outputs.get("13", {})
        images_list = save_node_output.get("images", [])

        if not images_list:
            # Try to find any node with images output
            for node_id, node_output in outputs.items():
                if "images" in node_output and node_output["images"]:
                    images_list = node_output["images"]
                    break

        if not images_list:
            raise Exception(f"No images in output: {outputs}")

        image_info = images_list[0]
        filename = image_info.get("filename")
        subfolder = image_info.get("subfolder", "")
        img_type = image_info.get("type", "output")

        if not filename:
            raise Exception(f"No filename in image info: {image_info}")

        print(f"[ComfyUI] Job completed in {elapsed:.0f}s — downloading {filename}")

        # 3. Download the generated image via /view endpoint
        view_params = {"filename": filename, "type": img_type}
        if subfolder:
            view_params["subfolder"] = subfolder

        view_resp = requests.get(
            f"{COMFYUI_URL}/view",
            params=view_params,
            timeout=60,
        )

        if view_resp.status_code != 200:
            raise Exception(f"Failed to download image ({view_resp.status_code}): {view_resp.text[:200]}")

        img_bytes = view_resp.content
        if len(img_bytes) < 1000:
            raise Exception(f"Image too small ({len(img_bytes)} bytes), likely an error")

        print(f"[ComfyUI] Image downloaded: {len(img_bytes) / 1024:.0f} KB")
        return img_bytes


def test_endpoint():
    """Test the ComfyUI endpoint via SSH tunnel."""
    print("[Test] Testing ComfyUI Z-Image Turbo (via SSH tunnel)...")
    print(f"[Test] ComfyUI URL: {COMFYUI_URL}")

    # Check connectivity first
    if not _check_comfyui():
        print("[Test] FAILED: Cannot reach ComfyUI. Is the SSH tunnel running?")
        print("[Test] On laptop: ssh -R 8001:localhost:8001 -p 2222 root@<VPS_IP>")
        return False

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

    all_prompts = creative_data.get("prompts", [])
    
    # Only process z_image prompts (character photos). Nano Banana prompts go to nano_banana_generator.py
    prompts = [
        p for p in all_prompts
        if p.get("generator", "z_image") == "z_image"
    ]
    
    skipped_nano = len(all_prompts) - len(prompts)
    if skipped_nano > 0:
        print(f"[ComfyUI] Skipping {skipped_nano} nano_banana prompts (handled by nano_banana_generator.py)")

    if limit:
        prompts = prompts[:limit]

    # Check ComfyUI connectivity before starting batch
    if not _check_comfyui():
        print("[ERROR] Cannot reach ComfyUI. Is the SSH tunnel running?")
        print("[ERROR] On laptop: ssh -R 8001:localhost:8001 -p 2222 root@<VPS_IP>")
        sys.exit(1)

    print(f"[ComfyUI] Generating {len(prompts)} Z-Image character images for @{username}...")

    output_dir = os.path.join(WORKSPACE, "output", "photos", username)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for i, p in enumerate(prompts):
        prompt_text = p.get("prompt", "")
        concept = p.get("concept", f"image_{i + 1}")
        print(f"\n[{i + 1}/{len(prompts)}] Generating: {concept}")
        print(f"  Prompt: {prompt_text[:100]}...")

        try:
            # Use per-prompt content_type for correct aspect ratio (4:5 for feed, 9:16 for story/reel)
            prompt_ct = p.get("content_type", "feed")
            img_bytes = generate_image(prompt_text, content_type=prompt_ct)
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
    print(f"\n[ComfyUI] Generation complete: {log['total_success']}/{log['total_requested']} successful")
    print(f"[ComfyUI] Log saved: {log_path}")

    return log


def main():
    parser = argparse.ArgumentParser(description="Z-Image Turbo Generator (ComfyUI via SSH tunnel)")
    parser.add_argument("username", nargs="?", help="Generate from creative_prompts/<username>_prompts.json")
    parser.add_argument("--prompt", help="Generate a single image from a prompt")
    parser.add_argument("--test", action="store_true", help="Test endpoint with a simple prompt")
    parser.add_argument("--limit", type=int, help="Limit number of images to generate")
    parser.add_argument("--content-type", choices=["feed", "story", "reel", "square", "default"],
                        default="default", help="Content type: feed (4:5), story/reel (9:16), square (1:1)")

    args = parser.parse_args()

    if args.test:
        ok = test_endpoint()
        sys.exit(0 if ok else 1)

    if args.prompt:
        if not args.prompt.startswith("A w1man"):
            print("[WARN] Prompt should start with 'A w1man, ' for LoRA activation")
            args.prompt = "A w1man, " + args.prompt
        try:
            img_bytes = generate_image(args.prompt, content_type=args.content_type)
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
