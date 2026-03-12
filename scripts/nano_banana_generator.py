#!/usr/bin/env python3
"""
Nano Banana 2 Generator — generates non-character images via Evolink API.

Used for carousel slides that don't contain the main character (backgrounds,
objects, flat-lays, food, products, landscapes, etc.).

Usage:
  python3 nano_banana_generator.py <username>           # generate from creative_prompts/<username>_prompts.json
  python3 nano_banana_generator.py --prompt "A ..."     # generate a single image from a prompt
  python3 nano_banana_generator.py --test               # test endpoint with a simple prompt
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")

EVOLINK_API_KEY = os.environ.get("EVOLINK_API_KEY", "")
EVOLINK_BASE_URL = "https://api.evolink.ai/v1"

# Model options: nano-banana-2-beta (full quality), nano-banana-2-lite (faster, cheaper)
DEFAULT_MODEL = "nano-banana-2-beta"

# Supported aspect ratios: auto, 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1
# Instagram: 4:5 for feed posts, 9:16 for stories/reels, 1:1 for square
SIZE_MAP = {
    "portrait": "4:5",
    "story": "9:16",
    "square": "1:1",
    "landscape": "16:9",
    "wide": "21:9",
    "tall": "3:4",
}


def generate_image(prompt, size="4:5", model=DEFAULT_MODEL, poll_interval=10, max_wait=600):
    """
    Submit an image generation task to Evolink and poll until complete.
    Returns PNG/JPG image bytes on success, raises Exception on failure.
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    # Resolve size alias
    resolved_size = SIZE_MAP.get(size, size)

    print(f"[NanoBanana] Submitting to {model} (size={resolved_size})...")

    # 1. Submit task
    resp = requests.post(
        f"{EVOLINK_BASE_URL}/images/generations",
        headers=headers,
        json={
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": resolved_size,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"Evolink submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    status = task_data.get("status", "unknown")

    if not task_id:
        raise Exception(f"No task ID in response: {task_data}")

    print(f"[NanoBanana] Task: {task_id} (status={status})")
    estimated = task_data.get("task_info", {}).get("estimated_time", "?")
    print(f"[NanoBanana] Estimated time: {estimated}s")

    # If completed immediately (unlikely for image gen but possible)
    if status in ("completed", "success"):
        return _extract_image(task_data)

    # 2. Poll for result
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for task {task_id}")

        time.sleep(poll_interval)

        status_resp = requests.get(
            f"{EVOLINK_BASE_URL}/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )

        if status_resp.status_code != 200:
            print(f"[NanoBanana] Status check failed ({status_resp.status_code}), retrying...")
            continue

        data = status_resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)

        if status in ("completed", "success"):
            print(f"[NanoBanana] Completed in {elapsed:.0f}s")
            return _extract_image(data)

        elif status in ("failed", "error"):
            error = data.get("error", data)
            raise Exception(f"Task failed: {error}")

        elif status in ("pending", "processing", "running"):
            print(f"[NanoBanana] {status} (progress={progress}, {elapsed:.0f}s elapsed)...")

        else:
            print(f"[NanoBanana] Unknown status: {status} ({elapsed:.0f}s elapsed)")


def _extract_image(data):
    """Extract image bytes from completed task response."""
    # Evolink returns results in "results" key (list of URLs)
    images = data.get("results", [])
    if not images:
        images = data.get("data", [])
    if not images:
        # Try alternative response structures
        output = data.get("output", {})
        images = output.get("images", output.get("data", []))
    if not images:
        raise Exception(f"No images in response: {json.dumps(data)[:500]}")

    url = None
    if isinstance(images, list) and len(images) > 0:
        item = images[0]
        if isinstance(item, dict):
            url = item.get("url") or item.get("image_url")
        elif isinstance(item, str):
            url = item

    if not url:
        raise Exception(f"Could not extract image URL: {images}")

    print(f"[NanoBanana] Downloading image from {url[:80]}...")
    img_resp = requests.get(url, timeout=120)
    if img_resp.status_code != 200:
        raise Exception(f"Failed to download image ({img_resp.status_code})")

    return img_resp.content


def test_endpoint():
    """Test the Nano Banana 2 endpoint with a simple prompt."""
    print("[Test] Testing Nano Banana 2 endpoint...")
    print(f"[Test] API Key: {EVOLINK_API_KEY[:12]}...")
    print(f"[Test] Model: {DEFAULT_MODEL}")

    test_prompt = (
        "Professional product photography of a ceramic coffee cup on a white marble table, "
        "shot with a Canon EOS R5 85mm f/1.4 lens, shallow depth of field, "
        "soft diffused natural morning light from a large window to the left, "
        "gentle steam wisps rising from the cup, subtle shadow on the marble surface, "
        "minimalist Scandinavian cafe interior blurred in the background, "
        "warm color temperature 5600K, no filters, no illustration, "
        "ultra high resolution RAW photograph, 8K detail, real materials real textures"
    )
    print(f"[Test] Prompt: {test_prompt[:100]}...")

    try:
        img_bytes = generate_image(test_prompt, size="1:1")
        output_dir = os.path.join(WORKSPACE, "output", "photos")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "nano_test.png")
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        size_kb = len(img_bytes) / 1024
        print(f"[Test] SUCCESS! Image saved: {output_path} ({size_kb:.0f} KB)")
        return True
    except Exception as e:
        print(f"[Test] FAILED: {e}")
        return False


def generate_from_prompts(username, limit=None, size="4:5"):
    """Generate non-character images from creative prompts for a given username."""
    prompts_path = os.path.join(WORKSPACE, "creative_prompts", f"{username}_prompts.json")
    if not os.path.exists(prompts_path):
        print(f"[ERROR] Creative prompts not found: {prompts_path}")
        sys.exit(1)

    with open(prompts_path) as f:
        creative_data = json.load(f)

    # Filter prompts that are marked as non-character (no w1man trigger)
    all_prompts = creative_data.get("prompts", [])
    non_char_prompts = [
        p for p in all_prompts
        if p.get("generator") == "nano_banana"
        or p.get("type") == "background"
        or not p.get("prompt", "").startswith("A w1man")
    ]

    if not non_char_prompts:
        print(f"[NanoBanana] No non-character prompts found for @{username}")
        print(f"[NanoBanana] All {len(all_prompts)} prompts are character-based (use runpod_generator.py)")
        return None

    if limit:
        non_char_prompts = non_char_prompts[:limit]

    print(f"[NanoBanana] Generating {len(non_char_prompts)} non-character images for @{username}...")

    output_dir = os.path.join(WORKSPACE, "output", "photos", username)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for i, p in enumerate(non_char_prompts):
        prompt_text = p.get("prompt", "")
        concept = p.get("concept", f"bg_image_{i + 1}")
        img_size = p.get("size", size)
        print(f"\n[{i + 1}/{len(non_char_prompts)}] Generating: {concept}")
        print(f"  Prompt: {prompt_text[:100]}...")

        try:
            img_bytes = generate_image(prompt_text, size=img_size)
            safe_concept = concept.replace(" ", "_").replace("/", "_")[:50]
            filename = f"{username}_nano_{safe_concept}_{i + 1}.png"
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
                "generator": "nano_banana_2",
                "status": "success",
            })
        except Exception as e:
            print(f"  [FAIL] {e}")
            results.append({
                "concept": concept,
                "prompt": prompt_text,
                "file": None,
                "generator": "nano_banana_2",
                "status": "failed",
                "error": str(e),
            })

    # Save generation log
    log = {
        "username": username,
        "generator": "nano_banana_2",
        "model": DEFAULT_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_requested": len(non_char_prompts),
        "total_success": sum(1 for r in results if r["status"] == "success"),
        "total_failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    log_path = os.path.join(output_dir, "nano_generation_log.json")
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"\n[NanoBanana] Complete: {log['total_success']}/{log['total_requested']} successful")
    print(f"[NanoBanana] Log saved: {log_path}")

    return log


def main():
    parser = argparse.ArgumentParser(description="Nano Banana 2 Generator (non-character images)")
    parser.add_argument("username", nargs="?", help="Generate from creative_prompts/<username>_prompts.json")
    parser.add_argument("--prompt", help="Generate a single image from a prompt")
    parser.add_argument("--test", action="store_true", help="Test endpoint with a simple prompt")
    parser.add_argument("--limit", type=int, help="Limit number of images to generate")
    parser.add_argument("--size", default="4:5", help="Aspect ratio (portrait/story/square/landscape or 4:5/9:16/1:1)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to use (nano-banana-2-beta or nano-banana-2-lite)")

    args = parser.parse_args()

    if args.test:
        ok = test_endpoint()
        sys.exit(0 if ok else 1)

    if args.prompt:
        try:
            img_bytes = generate_image(args.prompt, size=args.size, model=args.model)
            output_dir = os.path.join(WORKSPACE, "output", "photos")
            os.makedirs(output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"nano_single_{ts}.png")
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            print(f"[OK] Image saved: {output_path} ({len(img_bytes) / 1024:.0f} KB)")
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.username:
        generate_from_prompts(args.username, limit=args.limit, size=args.size)
        sys.exit(0)

    parser.error("Provide a username, --prompt, or --test")


if __name__ == "__main__":
    main()
