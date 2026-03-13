#!/usr/bin/env python3
"""
Kling Motion Control — generates video reels via Evolink API (Kling v3).

Supports:
  - Text-to-video (kling-v3-text-to-video)
  - Image-to-video (kling-v3-image-to-video) — start frame + prompt
  - Motion control (kling-v3-motion-control) — reference video movement transfer
  - Reference-to-video (kling-o3-reference-to-video) — character reference

For Instagram Reels: generates short videos (5-10s) with motion from reference.

Usage:
  python3 kling_motion_control.py --test                          # test with simple prompt
  python3 kling_motion_control.py --text-to-video "A woman ..."   # text-to-video
  python3 kling_motion_control.py --image-to-video /path/img.png "dance prompt"  # img + prompt
  python3 kling_motion_control.py --motion-control /path/ref.mp4 /path/start.png  # motion transfer
  python3 kling_motion_control.py <username>                      # from director brief reel items
"""

import argparse
import base64
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")

EVOLINK_API_KEY = os.environ.get("EVOLINK_API_KEY", "")
EVOLINK_BASE_URL = "https://api.evolink.ai/v1"

# Available Kling models on Evolink
MODELS = {
    "text-to-video": "kling-v3-text-to-video",
    "image-to-video": "kling-v3-image-to-video",
    "motion-control": "kling-v3-motion-control",
    "reference-to-video": "kling-o3-reference-to-video",
    "video-edit": "kling-o3-video-edit",
}


EVOLINK_FILES_URL = "https://files-api.evolink.ai"


def _encode_file_base64(filepath):
    """Read file and return base64 encoded string."""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _image_to_data_url(filepath):
    """Convert image file to data URL."""
    ext = os.path.splitext(filepath)[1].lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/png")
    b64 = _encode_file_base64(filepath)
    return f"data:{mime};base64,{b64}"


def _upload_file_to_evolink(filepath):
    """Upload a file to Evolink file service and return the public URL.
    
    Supports images via base64 upload and videos via stream upload.
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
    }
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    # Use stream upload (multipart/form-data) for all files
    print(f"[Kling] Uploading {filename} ({os.path.getsize(filepath) // 1024} KB) to Evolink files...")
    with open(filepath, "rb") as f:
        files = {"file": (filename, f)}
        data = {"file_name": filename}
        resp = requests.post(
            f"{EVOLINK_FILES_URL}/api/v1/files/upload/stream",
            headers=headers,
            files=files,
            data=data,
            timeout=120,
        )

    if resp.status_code == 200:
        result = resp.json()
        if result.get("success"):
            file_url = result["data"]["file_url"]
            print(f"[Kling] Uploaded: {file_url}")
            return file_url

    # If stream upload fails (e.g. for video), try base64 upload
    print(f"[Kling] Stream upload failed ({resp.status_code}: {resp.text[:200]}), trying base64...")
    b64 = _encode_file_base64(filepath)
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime",
    }
    mime = mime_map.get(ext, "application/octet-stream")
    data_url = f"data:{mime};base64,{b64}"

    resp2 = requests.post(
        f"{EVOLINK_FILES_URL}/api/v1/files/upload/base64",
        headers={**headers, "Content-Type": "application/json"},
        json={"base64_data": data_url, "file_name": filename},
        timeout=120,
    )

    if resp2.status_code == 200:
        result = resp2.json()
        if result.get("success"):
            file_url = result["data"]["file_url"]
            print(f"[Kling] Uploaded via base64: {file_url}")
            return file_url

    raise Exception(f"Failed to upload file to Evolink: stream={resp.status_code}, base64={resp2.status_code}: {resp2.text[:300]}")


def _poll_video_task(task_id, task_type="videos", poll_interval=15, max_wait=600):
    """Poll Evolink for video generation task completion."""
    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

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
            print(f"[Kling] Status check failed ({status_resp.status_code}), retrying...")
            continue

        data = status_resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)

        if status in ("completed", "success"):
            print(f"[Kling] Completed in {elapsed:.0f}s")
            return data

        elif status in ("failed", "error"):
            error = data.get("error", data)
            raise Exception(f"Task failed: {error}")

        elif status in ("pending", "processing", "running"):
            print(f"[Kling] {status} (progress={progress}, {elapsed:.0f}s elapsed)...")

        else:
            print(f"[Kling] Unknown status: {status} ({elapsed:.0f}s elapsed)")


def _extract_video(data):
    """Extract video URL/bytes from completed task response."""
    # Evolink returns results in "results" key (list of URLs)
    videos = data.get("results", [])
    if not videos:
        videos = data.get("data", [])
    if not videos:
        output = data.get("output", {})
        videos = output.get("videos", output.get("data", []))
    if not videos:
        raise Exception(f"No videos in response: {json.dumps(data)[:500]}")

    url = None
    if isinstance(videos, list) and len(videos) > 0:
        item = videos[0]
        if isinstance(item, dict):
            url = item.get("url") or item.get("video_url")
        elif isinstance(item, str):
            url = item

    if not url:
        raise Exception(f"Could not extract video URL: {videos}")

    print(f"[Kling] Downloading video from {url[:80]}...")
    vid_resp = requests.get(url, timeout=300)
    if vid_resp.status_code != 200:
        raise Exception(f"Failed to download video ({vid_resp.status_code})")

    return vid_resp.content, url


def text_to_video(prompt, duration=5, aspect_ratio="9:16"):
    """Generate video from text prompt using Kling v3."""
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    model = MODELS["text-to-video"]
    print(f"[Kling] Text-to-video ({model}, {duration}s, {aspect_ratio})...")

    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
    }

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"Evolink submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    status = task_data.get("status", "unknown")
    print(f"[Kling] Task: {task_id} (status={status})")
    estimated = task_data.get("task_info", {}).get("estimated_time", "?")
    print(f"[Kling] Estimated time: {estimated}s")

    if status in ("completed", "success"):
        return _extract_video(task_data)

    data = _poll_video_task(task_id, poll_interval=15, max_wait=600)
    return _extract_video(data)


def image_to_video(image_path, prompt, duration=5):
    """Generate video from starting image + prompt using Kling v3."""
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")
    if not os.path.exists(image_path):
        raise Exception(f"Image not found: {image_path}")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    model = MODELS["image-to-video"]
    image_data_url = _image_to_data_url(image_path)
    print(f"[Kling] Image-to-video ({model}, start frame: {image_path})...")

    payload = {
        "model": model,
        "prompt": prompt,
        "image": image_data_url,
        "n": 1,
    }

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        raise Exception(f"Evolink submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    status = task_data.get("status", "unknown")
    print(f"[Kling] Task: {task_id} (status={status})")

    if status in ("completed", "success"):
        return _extract_video(task_data)

    data = _poll_video_task(task_id, poll_interval=15, max_wait=600)
    return _extract_video(data)


def motion_control(reference_video_path, start_image_path, prompt="", character_orientation="image"):
    """
    Generate video using motion control — transfers movement from reference video
    to the character in the start image.

    Args:
        reference_video_path: Path to reference video (motion source) — local path or URL
        start_image_path: Path to start frame image (character) — local path or URL
        prompt: Additional text prompt (optional)
        character_orientation: 'image' (from start image) or 'video' (from reference video)
    """
    if not EVOLINK_API_KEY:
        raise Exception("EVOLINK_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {EVOLINK_API_KEY}",
        "Content-Type": "application/json",
    }

    model = MODELS["motion-control"]
    print(f"[Kling] Motion control ({model})...")
    print(f"[Kling] Reference video: {reference_video_path}")
    print(f"[Kling] Start image: {start_image_path}")

    # Upload local files to Evolink file service to get public URLs
    # (Kling API requires image_urls and video_urls as publicly accessible URLs)
    if reference_video_path.startswith("http"):
        video_url = reference_video_path
    else:
        if not os.path.exists(reference_video_path):
            raise Exception(f"Reference video not found: {reference_video_path}")
        video_url = _upload_file_to_evolink(reference_video_path)

    if start_image_path.startswith("http"):
        image_url = start_image_path
    else:
        if not os.path.exists(start_image_path):
            raise Exception(f"Start image not found: {start_image_path}")
        image_url = _upload_file_to_evolink(start_image_path)

    payload = {
        "model": model,
        "image_urls": [image_url],
        "video_urls": [video_url],
        "quality": "720p",
        "model_params": {
            "character_orientation": character_orientation,
        },
    }
    if prompt:
        payload["prompt"] = prompt

    resp = requests.post(
        f"{EVOLINK_BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=120,
    )

    if resp.status_code != 200:
        raise Exception(f"Evolink submit failed ({resp.status_code}): {resp.text[:500]}")

    task_data = resp.json()
    task_id = task_data.get("id")
    status = task_data.get("status", "unknown")
    print(f"[Kling] Task: {task_id} (status={status})")
    estimated = task_data.get("task_info", {}).get("estimated_time", "?")
    print(f"[Kling] Estimated time: {estimated}s")

    if status in ("completed", "success"):
        return _extract_video(task_data)

    data = _poll_video_task(task_id, poll_interval=20, max_wait=900)
    return _extract_video(data)


def analyze_reference_video(reference_video_path):
    """
    Analyze a reference video to extract motion/pose/camera information.
    Uses OpenRouter vision API to describe the first frame and motion pattern.
    Returns a dict with analysis that can be used to generate a matching Z-Image frame.
    
    Workflow:
      1. Extract first frame description from reference video
      2. Determine camera distance (close-up / medium / full body)
      3. Determine character orientation (facing camera / side / back)
      4. Determine pose (standing / sitting / walking / dancing)
      5. Generate a Z-Image prompt that matches the reference video's first frame
    """
    import subprocess as sp
    
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    if not OPENROUTER_API_KEY:
        # Try loading from .env
        try:
            from dotenv import load_dotenv
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
                OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
        except ImportError:
            pass
    
    if not OPENROUTER_API_KEY:
        print("[Kling] WARNING: OPENROUTER_API_KEY not set, skipping video analysis")
        return None
    
    # Try to extract first frame using ffmpeg
    first_frame_path = None
    temp_frame = os.path.join(WORKSPACE, "temp_ref_frame.jpg")
    try:
        result = sp.run(
            ["ffmpeg", "-y", "-i", reference_video_path, "-frames:v", "1",
             "-q:v", "2", temp_frame],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(temp_frame):
            first_frame_path = temp_frame
            print(f"[Kling] Extracted first frame from reference video")
    except Exception as e:
        print(f"[Kling] Could not extract frame with ffmpeg: {e}")
    
    if not first_frame_path:
        print("[Kling] No first frame extracted, using text-only analysis")
        return {
            "camera_distance": "medium",
            "orientation": "facing_camera",
            "pose": "standing",
            "z_image_prompt_hint": "medium shot, facing camera, natural pose",
            "analyzed": False,
        }
    
    # Analyze frame with vision API
    import base64 as b64mod
    with open(first_frame_path, "rb") as f:
        frame_b64 = b64mod.b64encode(f.read()).decode("utf-8")
    
    analysis_prompt = (
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
    
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.0-flash-lite-001",
                "messages": [
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{frame_b64}"
                        }},
                        {"type": "text", "text": analysis_prompt},
                    ]}
                ],
                "temperature": 0.3,
                "max_tokens": 500,
            },
            timeout=60,
        )
        
        if resp.status_code == 200:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            # Parse JSON from response
            import re
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
                analysis["analyzed"] = True
                print(f"[Kling] Reference video analysis: distance={analysis.get('camera_distance')}, "
                      f"pose={analysis.get('pose')}, orientation={analysis.get('orientation')}")
                # Clean up temp file
                if os.path.exists(temp_frame):
                    os.remove(temp_frame)
                return analysis
    except Exception as e:
        print(f"[Kling] Vision analysis failed: {e}")
    
    # Clean up temp file
    if os.path.exists(temp_frame):
        os.remove(temp_frame)
    
    return {
        "camera_distance": "medium",
        "orientation": "facing_camera",
        "pose": "standing",
        "z_image_prompt_hint": "medium shot, facing camera, natural pose",
        "analyzed": False,
    }


def generate_reels_from_brief(username, limit=None):
    """Generate reels from director brief reel items."""
    brief_path = os.path.join(WORKSPACE, "director_briefs", f"{username}_brief.json")
    if not os.path.exists(brief_path):
        print(f"[ERROR] Director brief not found: {brief_path}")
        sys.exit(1)

    with open(brief_path) as f:
        brief = json.load(f)

    reel_items = [
        item for item in brief.get("items", brief.get("content_plan", []))
        if item.get("type") == "reel" or item.get("format") == "reel"
    ]

    if not reel_items:
        print(f"[Kling] No reel items found in brief for @{username}")
        return None

    if limit:
        reel_items = reel_items[:limit]

    print(f"[Kling] Generating {len(reel_items)} reels for @{username}...")

    output_dir = os.path.join(WORKSPACE, "output", "reels", username)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for i, item in enumerate(reel_items):
        prompt = item.get("prompt", item.get("description", ""))
        concept = item.get("concept", f"reel_{i + 1}")
        ref_video = item.get("motion_reference", item.get("reference_video"))
        start_img = item.get("start_frame", item.get("start_image"))

        print(f"\n[{i + 1}/{len(reel_items)}] Generating reel: {concept}")

        try:
            # If we have a reference video, analyze it first to generate matching Z-Image frame
            if ref_video and os.path.exists(ref_video) and not start_img:
                print(f"  [Kling] Analyzing reference video to generate matching first frame...")
                analysis = analyze_reference_video(ref_video)
                if analysis:
                    # Save analysis for Telegram preview
                    analysis_path = os.path.join(output_dir, f"ref_analysis_{i + 1}.json")
                    with open(analysis_path, "w") as af:
                        json.dump(analysis, af, indent=2)
                    print(f"  [Kling] Reference analysis saved: {analysis_path}")
                    # The Z-Image frame generation will be triggered separately
                    # (pipeline_runner handles the Z-Image -> Telegram approval -> Kling flow)
                    item["reference_analysis"] = analysis

            if ref_video and start_img and os.path.exists(ref_video) and os.path.exists(start_img):
                # Motion control mode
                video_bytes, video_url = motion_control(ref_video, start_img, prompt=prompt)
            elif start_img and os.path.exists(start_img):
                # Image-to-video mode
                video_bytes, video_url = image_to_video(start_img, prompt)
            else:
                # Text-to-video fallback
                video_bytes, video_url = text_to_video(prompt)

            safe_concept = concept.replace(" ", "_").replace("/", "_")[:50]
            filename = f"{username}_reel_{safe_concept}_{i + 1}.mp4"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(video_bytes)

            size_mb = len(video_bytes) / (1024 * 1024)
            print(f"  [OK] Saved: {filepath} ({size_mb:.1f} MB)")
            results.append({
                "concept": concept,
                "prompt": prompt,
                "file": filepath,
                "size_bytes": len(video_bytes),
                "url": video_url,
                "status": "success",
            })
        except Exception as e:
            print(f"  [FAIL] {e}")
            results.append({
                "concept": concept,
                "prompt": prompt,
                "file": None,
                "status": "failed",
                "error": str(e),
            })

    # Save generation log
    log = {
        "username": username,
        "generator": "kling_motion_control",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_requested": len(reel_items),
        "total_success": sum(1 for r in results if r["status"] == "success"),
        "total_failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    log_path = os.path.join(output_dir, "reel_generation_log.json")
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"\n[Kling] Complete: {log['total_success']}/{log['total_requested']} successful")
    print(f"[Kling] Log saved: {log_path}")

    return log


def test_endpoint():
    """Test the Kling text-to-video endpoint with a simple prompt."""
    print("[Test] Testing Kling v3 text-to-video endpoint...")
    print(f"[Test] API Key: {EVOLINK_API_KEY[:12]}...")
    print(f"[Test] Model: {MODELS['text-to-video']}")

    test_prompt = (
        "A woman in a flowing white dress walking along a beach at sunset, "
        "wind gently blowing her hair, golden hour lighting, cinematic, "
        "smooth camera follow shot, 9:16 vertical format"
    )
    print(f"[Test] Prompt: {test_prompt[:100]}...")

    try:
        video_bytes, video_url = text_to_video(test_prompt, duration=5)
        output_dir = os.path.join(WORKSPACE, "output", "reels")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "kling_test.mp4")
        with open(output_path, "wb") as f:
            f.write(video_bytes)
        size_mb = len(video_bytes) / (1024 * 1024)
        print(f"[Test] SUCCESS! Video saved: {output_path} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"[Test] FAILED: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Kling Motion Control (video reels via Evolink)")
    parser.add_argument("username", nargs="?", help="Generate reels from director brief")
    parser.add_argument("--test", action="store_true", help="Test text-to-video endpoint")
    parser.add_argument("--text-to-video", metavar="PROMPT", help="Generate video from text prompt")
    parser.add_argument("--image-to-video", nargs=2, metavar=("IMAGE", "PROMPT"),
                        help="Generate video from start image + prompt")
    parser.add_argument("--motion-control", nargs=2, metavar=("REF_VIDEO", "START_IMAGE"),
                        help="Motion control: transfer movement from reference video to start image")
    parser.add_argument("--prompt", default="", help="Additional prompt for motion control")
    parser.add_argument("--orientation", default="image",
                        choices=["image", "video"],
                        help="Character orientation source: 'image' (from start image) or 'video' (from reference video)")
    parser.add_argument("--limit", type=int, help="Limit number of reels to generate")

    args = parser.parse_args()

    if args.test:
        ok = test_endpoint()
        sys.exit(0 if ok else 1)

    output_dir = os.path.join(WORKSPACE, "output", "reels")
    os.makedirs(output_dir, exist_ok=True)

    if args.text_to_video:
        try:
            video_bytes, video_url = text_to_video(args.text_to_video)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"kling_t2v_{ts}.mp4")
            with open(output_path, "wb") as f:
                f.write(video_bytes)
            print(f"[OK] Video saved: {output_path} ({len(video_bytes) / (1024 * 1024):.1f} MB)")
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.image_to_video:
        image_path, prompt = args.image_to_video
        try:
            video_bytes, video_url = image_to_video(image_path, prompt)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"kling_i2v_{ts}.mp4")
            with open(output_path, "wb") as f:
                f.write(video_bytes)
            print(f"[OK] Video saved: {output_path} ({len(video_bytes) / (1024 * 1024):.1f} MB)")
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.motion_control:
        ref_video, start_image = args.motion_control
        try:
            video_bytes, video_url = motion_control(
                ref_video, start_image,
                prompt=args.prompt,
                character_orientation=args.orientation
            )
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"kling_mc_{ts}.mp4")
            with open(output_path, "wb") as f:
                f.write(video_bytes)
            print(f"[OK] Video saved: {output_path} ({len(video_bytes) / (1024 * 1024):.1f} MB)")
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.username:
        generate_reels_from_brief(args.username, limit=args.limit)
        sys.exit(0)

    parser.error("Provide a username, --text-to-video, --image-to-video, --motion-control, or --test")


if __name__ == "__main__":
    main()
