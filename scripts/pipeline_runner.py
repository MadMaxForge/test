#!/usr/bin/env python3
"""
Instagram Pipeline Runner - full automation:
  Scrape > Scout > Director > Creative > Generate > QC > Publish (> Telegram preview)

Content types:
  --content-type feed    : carousel/feed posts (4:5 aspect ratio)
  --content-type story   : stories (9:16)
  --content-type reel    : reels (9:16) - includes Kling Motion Control step

Usage: python3 pipeline_runner.py <username> [--count N] [--content-type TYPE]
       [--skip-scrape] [--skip-generate] [--limit-images N] [--skip-telegram]
       [--reference-video PATH] [--start-image PATH]
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
SCRIPTS = os.path.join(WORKSPACE, "scripts")


def run_step(name, cmd):
    print(f"\n{'=' * 50}")
    print(f"  STEP: {name}")
    print(f"{'=' * 50}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=900)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        print(f"[FAIL] {name} failed with exit code {result.returncode}")
        return False
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline_runner.py <username> [--count N] [--skip-scrape] [--skip-generate] [--limit-images N]")
        sys.exit(1)

    username = sys.argv[1]
    count = 12
    content_type = "feed"  # default
    skip_scrape = "--skip-scrape" in sys.argv
    skip_generate = "--skip-generate" in sys.argv
    skip_telegram = "--skip-telegram" in sys.argv
    limit_images = None
    reference_video = None
    start_image = None

    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        count = int(sys.argv[idx + 1])

    if "--content-type" in sys.argv:
        idx = sys.argv.index("--content-type")
        content_type = sys.argv[idx + 1]

    if "--limit-images" in sys.argv:
        idx = sys.argv.index("--limit-images")
        limit_images = int(sys.argv[idx + 1])

    if "--reference-video" in sys.argv:
        idx = sys.argv.index("--reference-video")
        reference_video = sys.argv[idx + 1]

    if "--start-image" in sys.argv:
        idx = sys.argv.index("--start-image")
        start_image = sys.argv[idx + 1]

    print(f"[Pipeline] Starting for @{username} ({count} posts, content_type={content_type})")
    print(f"[Pipeline] Workspace: {WORKSPACE}")
    if content_type == "reel":
        print(f"[Pipeline] Reel mode: will generate Z-Image frame + Kling Motion Control video")
        if reference_video:
            print(f"[Pipeline] Reference video: {reference_video}")
    if skip_scrape:
        print("[Pipeline] Skipping scrape step (using existing data)")
    if skip_generate:
        print("[Pipeline] Skipping image generation step")
    start = time.time()

    # Ensure API key is available
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        env_file = os.path.join(os.path.dirname(WORKSPACE), ".env")
        if os.path.exists(env_file):
            for line in open(env_file):
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.strip().split("=", 1)[1]
                    os.environ["OPENROUTER_API_KEY"] = api_key

    if not api_key:
        print("[FAIL] OPENROUTER_API_KEY not found")
        sys.exit(1)

    # Step 1: Scrape (optional)
    manifest_path = os.path.join(WORKSPACE, "downloads", username, "manifest.json")
    if not skip_scrape:
        ok = run_step(
            "Instagram Scraper (instagrapi)",
            f"cd {WORKSPACE} && python3 {SCRIPTS}/instagram_scraper.py {username} --count {count}"
        )
        if not ok:
            sys.exit(1)

    # Verify manifest
    if not os.path.exists(manifest_path):
        print(f"[FAIL] Manifest not found at {manifest_path}")
        if skip_scrape:
            print("[HINT] Remove --skip-scrape to download data first")
        sys.exit(1)
    manifest = json.load(open(manifest_path))
    print(f"[OK] Manifest: {manifest['total_posts_scraped']} posts")

    # Step 2: Scout Analysis
    ok = run_step(
        "Scout Agent (AI Analysis)",
        f"cd {WORKSPACE} && python3 {SCRIPTS}/scout_agent.py {username}"
    )
    if not ok:
        sys.exit(1)

    analysis_path = os.path.join(WORKSPACE, "scout_analysis", f"{username}_analysis.json")
    if not os.path.exists(analysis_path):
        print("[FAIL] Scout analysis not created")
        sys.exit(1)
    analysis = json.load(open(analysis_path))
    summary = analysis.get("profile_summary", "N/A")[:100]
    print(f"[OK] Scout analysis: {summary}...")

    # Step 3: Director Brief
    ok = run_step(
        "Director Agent (Strategic Brief)",
        f"cd {WORKSPACE} && python3 {SCRIPTS}/director_agent.py {username}"
    )
    if not ok:
        sys.exit(1)

    brief_path = os.path.join(WORKSPACE, "director_briefs", f"{username}_brief.json")
    if not os.path.exists(brief_path):
        print("[FAIL] Director brief not created")
        sys.exit(1)
    brief = json.load(open(brief_path))
    summary = brief.get("executive_summary", "N/A")[:100]
    print(f"[OK] Director brief: {summary}...")

    # Step 4: Creative Agent (Prompt Generation)
    ok = run_step(
        "Creative Agent (Image Prompts)",
        f"cd {WORKSPACE} && python3 {SCRIPTS}/creative_agent.py {username}"
    )
    if not ok:
        sys.exit(1)

    prompts_path = os.path.join(WORKSPACE, "creative_prompts", f"{username}_prompts.json")
    if not os.path.exists(prompts_path):
        print("[FAIL] Creative prompts not created")
        sys.exit(1)
    prompts_data = json.load(open(prompts_path))
    total_prompts = prompts_data.get("total_prompts", 0)
    print(f"[OK] Creative prompts: {total_prompts} prompts generated")

    # Step 4b: Archive old images before generating new ones
    if not skip_generate:
        photos_dir = os.path.join(WORKSPACE, "output", "photos", username)
        if os.path.exists(photos_dir):
            old_images = list(Path(photos_dir).glob("*.png")) + list(Path(photos_dir).glob("*.jpg"))
            if old_images:
                archive_date = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
                archive_dir = os.path.join(photos_dir, "archive", archive_date)
                os.makedirs(archive_dir, exist_ok=True)
                for img in old_images:
                    shutil.move(str(img), os.path.join(archive_dir, img.name))
                print(f"[Pipeline] Archived {len(old_images)} old images to {archive_dir}")

    # Step 5: Image Generation via RunPod (optional)
    if not skip_generate:
        limit_flag = f" --limit {limit_images}" if limit_images else ""
        ok = run_step(
            f"RunPod Z-Image Turbo (Image Generation, {content_type})",
            f"cd {WORKSPACE} && python3 {SCRIPTS}/runpod_generator.py {username}{limit_flag}"
        )
        if not ok:
            print("[WARN] Image generation had issues, check logs above")

        log_path = os.path.join(WORKSPACE, "output", "photos", username, "generation_log.json")
        if os.path.exists(log_path):
            gen_log = json.load(open(log_path))
            print(f"[OK] Generation: {gen_log['total_success']}/{gen_log['total_requested']} images")
    else:
        print("\n[SKIP] Image generation skipped (--skip-generate)")

    # Step 5a: Nano Banana 2 (non-character images — backgrounds, products, etc.)
    if not skip_generate:
        nano_size = "4:5" if content_type == "feed" else "9:16"
        ok = run_step(
            "Nano Banana 2 (Non-character Images)",
            f"cd {WORKSPACE} && python3 {SCRIPTS}/nano_banana_generator.py {username} --size {nano_size}"
        )
        if not ok:
            print("[INFO] Nano Banana 2 step skipped or had no non-character prompts (this is normal)")
        else:
            nano_log_path = os.path.join(WORKSPACE, "output", "photos", username, "nano_generation_log.json")
            if os.path.exists(nano_log_path):
                nano_log = json.load(open(nano_log_path))
                print(f"[OK] Nano Banana: {nano_log['total_success']}/{nano_log['total_requested']} images")

    # Step 5b: Kling Motion Control (only for reels)
    kling_result = None
    if content_type == "reel" and not skip_generate:
        # For reels, we need a reference video and a start image (generated in step 5)
        if reference_video:
            # Use the first generated image as the start frame if not specified
            if not start_image:
                output_dir = os.path.join(WORKSPACE, "output", "photos", username)
                if os.path.exists(output_dir):
                    images = sorted(Path(output_dir).glob("*.png"))
                    if images:
                        start_image = str(images[0])
                        print(f"[Pipeline] Using generated image as start frame: {start_image}")

            if start_image:
                ok = run_step(
                    "Kling Motion Control (Video Generation)",
                    f"cd {WORKSPACE} && python3 {SCRIPTS}/kling_motion_control.py "
                    f"--motion-control {reference_video} {start_image} "
                    f"--character-orientation image"
                )
                if ok:
                    # Find the generated video
                    reels_dir = os.path.join(WORKSPACE, "output", "reels")
                    if os.path.exists(reels_dir):
                        videos = sorted(Path(reels_dir).glob("*.mp4"), reverse=True)
                        if videos:
                            kling_result = str(videos[0])
                            print(f"[OK] Kling video generated: {kling_result}")
                else:
                    print("[WARN] Kling Motion Control had issues, check logs above")
            else:
                print("[WARN] No start image available for Kling Motion Control")
        else:
            print("[WARN] No reference video provided for reel mode (use --reference-video PATH)")

    # Step 6: QC — Quality Check (only if images were generated)
    qc_report = None
    if not skip_generate:
        output_dir = os.path.join(WORKSPACE, "output", "photos", username)
        has_images = os.path.exists(output_dir) and any(
            f.endswith(('.png', '.jpg')) for f in os.listdir(output_dir)
        )
        if has_images:
            ok = run_step(
                "QC Agent (Quality Check)",
                f"cd {WORKSPACE} && python3 {SCRIPTS}/qc_agent.py {username}"
            )
            if not ok:
                print("[WARN] QC had issues, check logs above")

            qc_path = os.path.join(WORKSPACE, "qc_reports", f"{username}_qc.json")
            if os.path.exists(qc_path):
                qc_report = json.load(open(qc_path))
                print(f"[OK] QC: {qc_report['passed']}/{qc_report['total_images']} passed (avg {qc_report['average_score']})")
        else:
            print("\n[SKIP] QC skipped (no images found)")

    # Step 7: Publish Agent (assemble post package)
    post_path = None
    if not skip_generate and qc_report:
        ok = run_step(
            "Publish Agent (Carousel Assembly)",
            f"cd {WORKSPACE} && python3 {SCRIPTS}/publish_agent.py {username}"
        )
        if ok:
            posts_dir = os.path.join(WORKSPACE, "posts")
            if os.path.exists(posts_dir):
                post_files = sorted(Path(posts_dir).glob(f"{username}_post_*.json"), reverse=True)
                if post_files:
                    post_path = str(post_files[0])
                    post_data = json.load(open(post_path))
                    print(f"[OK] Post assembled: {post_data.get('image_count', 0)} images, caption ready")
        else:
            print("[WARN] Publish had issues, check logs above")

    # Step 8: Telegram Preview (optional)
    if post_path and not skip_telegram:
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        telegram_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if telegram_token and telegram_chat:
            ok = run_step(
                "Telegram Preview (Send for Review)",
                f"cd {WORKSPACE} && python3 {SCRIPTS}/telegram_preview.py {username}"
            )
            if not ok:
                print("[WARN] Telegram preview failed, check logs above")
        else:
            print("\n[SKIP] Telegram preview skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")

    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"  PIPELINE COMPLETE ({elapsed:.0f}s)")
    print(f"{'=' * 50}")
    print(f"  Profile: @{username}")
    print(f"  Content type: {content_type}")
    print(f"  Posts scraped: {manifest['total_posts_scraped']}")
    print(f"  Prompts generated: {total_prompts}")
    if qc_report:
        print(f"  QC: {qc_report['passed']}/{qc_report['total_images']} passed, avg score {qc_report['average_score']}")
    if post_path:
        post_data = json.load(open(post_path))
        print(f"  Post: {post_data.get('image_count', 0)} images, caption + {len(post_data.get('hashtags', []))} hashtags")
    if kling_result:
        print(f"  Reel video: {kling_result}")
    print(f"  Files:")
    print(f"    Manifest:  {manifest_path}")
    print(f"    Analysis:  {analysis_path}")
    print(f"    Brief:     {brief_path}")
    print(f"    Prompts:   {prompts_path}")
    if not skip_generate:
        output_dir = os.path.join(WORKSPACE, "output", "photos", username)
        print(f"    Images:    {output_dir}/")
    if qc_report:
        qc_path = os.path.join(WORKSPACE, "qc_reports", f"{username}_qc.json")
        print(f"    QC Report: {qc_path}")
    if post_path:
        print(f"    Post:      {post_path}")
    if kling_result:
        print(f"    Reel:      {kling_result}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
