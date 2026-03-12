# TOOLS.md — Instagram AI Pipeline Tools

## Scripts Directory
All scripts are located at: `/root/.openclaw/workspace/scripts/`

## Pipeline Tools

### 1. pipeline_runner.py — Full Pipeline Orchestrator
```bash
python3 scripts/pipeline_runner.py <username> [options]
```
Options:
- `--count N` — Number of posts to process (default: 1)
- `--content-type feed|story|reel|square` — Content type (default: feed)
- `--reference-video PATH` — Reference video for Kling Motion Control (reels only)
- `--start-image PATH` — Start image for Kling MC (auto-selected if not provided)
- `--skip-scrape` — Skip Instagram scraping step
- `--skip-generate` — Skip image/video generation
- `--skip-telegram` — Skip Telegram preview
- `--limit-images N` — Limit number of images to generate

### 2. instagram_scraper.py — Instagram Profile Scraper
```bash
python3 scripts/instagram_scraper.py <username> [--count N]
```
Downloads posts from Instagram profiles using Instaloader.

### 3. scout_agent.py — Visual Analysis (Vision)
```bash
python3 scripts/scout_agent.py <username>
```
Analyzes downloaded images with OpenRouter vision API (Kimi K2.5). Outputs structured JSON to `scout_analysis/`.

### 4. director_agent.py — Technical Brief Creation
```bash
python3 scripts/director_agent.py <username>
```
Creates technical generation briefs from Scout analysis. Outputs to `director_briefs/`.

### 5. creative_agent.py — Prompt Generation
```bash
python3 scripts/creative_agent.py <username>
```
Generates detailed Z-Image prompts following the w1man LoRA template. Supports content types: feed (4:5), story (9:16), reel (9:16). Outputs to `creative_prompts/`.

### 6. runpod_generator.py — Z-Image Character Generation
```bash
python3 scripts/runpod_generator.py <username> [--prompt "text"] [--content-type TYPE]
```
Generates character images via RunPod Z-Image Turbo endpoint with w1man LoRA. ComfyUI workflow with multiple LoRAs (w1man 1.0, DetailDaemon 0.5, Z-Breast-Slider 0.45).

Content type dimensions:
- feed: 1080x1350 (4:5)
- story/reel: 1088x1920 (9:16)
- square: 1024x1024 (1:1)

### 7. nano_banana_generator.py — Non-Character Image Generation
```bash
python3 scripts/nano_banana_generator.py <username> [--size 4:5|9:16|1:1] [--model nano-banana-2-beta|nano-banana-2-lite]
```
Generates backgrounds, products, and non-character images via Evolink Nano Banana 2 API.

### 8. kling_motion_control.py — Video Generation (Reels)
```bash
python3 scripts/kling_motion_control.py --motion-control <reference_video> <start_image> --character-orientation image
```
Generates motion-controlled video via Evolink Kling v3 API. Transfers motion from reference video onto Z-Image character. Character orientation: "image" for <=10s, "video" for <=30s.

**CRITICAL:** Start image camera distance MUST match reference video framing.

### 9. qc_agent.py — Quality Control
```bash
python3 scripts/qc_agent.py <username> [--threshold N]
```
Evaluates generated images on 5 criteria (0-10): prompt adherence, character consistency, technical quality, composition, content safety. Threshold >= 7 to pass.

### 10. publish_agent.py — Post Assembly
```bash
python3 scripts/publish_agent.py <username>
```
Assembles approved images into posts with captions and hashtags.

### 11. telegram_preview.py — Telegram Preview
```bash
python3 scripts/telegram_preview.py <username>
```
Sends carousel preview to Telegram for human approval with Approve/Reject buttons.

## Environment Variables (from .env)
- `OPENROUTER_API_KEY` — For LLM/vision tasks
- `RUNPOD_API_KEY` — For Z-Image generation
- `EVOLINK_API_KEY` — For Nano Banana 2 + Kling MC
- `TELEGRAM_BOT_TOKEN` — For preview bot
- `TELEGRAM_CHAT_ID` — Owner chat ID

## Output Directories
- `output/photos/<username>/` — Generated images
- `output/reels/<username>/` — Generated videos
- `scout_analysis/` — Scout analysis JSON
- `director_briefs/` — Director brief JSON
- `creative_prompts/` — Creative prompts JSON
- `qc_reports/` — QC reports JSON
- `posts/` — Assembled posts
