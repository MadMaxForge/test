# Content Creation Module for Instagram Character

Modular system for creating Instagram content (posts, stories, reels) for a consistent AI-generated character using the **w1man LoRA**.

## Architecture

```
Telegram (user)
    │
    ▼
MC Conductor (orchestrator)
    │
    ├── Reference Analyst (LLM) — analyzes downloaded references
    ├── Planner (LLM) — builds content plan (post/story/reel)
    ├── Creative (LLM) — writes prompts for generators
    ├── Publish (LLM) — generates captions & hashtags
    │
    ├── Z-Image Generator — character images (ComfyUI + w1man LoRA)
    ├── Nano Banana Generator — world/environment images (Evolink API)
    ├── Kling Generator — reel videos (Evolink API)
    │
    ├── State Manager — job state, versioning, locking
    ├── Queue Manager — reference URL queue
    └── Asset Router — deterministic routing to generators
```

## Content Types

| Type | Format | Generator(s) | Description |
|------|--------|-------------|-------------|
| **Post** | 4:5 | Z-Image + Nano Banana | Carousel up to 4 slides |
| **Story** | 9:16 | Z-Image + Nano Banana | Vertical frames, separate from posts |
| **Reel** | 9:16 | Z-Image → Kling | Start frame + motion transfer |

## Quick Start

### Prerequisites

- Python 3.11+
- ComfyUI running on laptop with SSH tunnel to VPS
- Telegram bot token + chat ID
- OpenRouter API key
- Evolink API key

### Setup

```bash
# Install dependencies
pip install python-telegram-bot==20.7 requests instagrapi

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with your keys

# Start SSH tunnel (on laptop)
ssh -R 8001:localhost:8001 -p 2222 root@<VPS_IP>

# Run the bot
python3 -m content_module
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/add <url>` | Add Instagram URL to reference queue |
| `/next` | Process next reference (auto-detect post/reel) |
| `/next_post` | Process next post reference |
| `/next_story [theme]` | Create story (by reference or theme) |
| `/next_reel` | Process next reel reference |
| `/status` | Show all jobs |
| `/queue` | Show reference queue |
| `/job <id>` | Show job details |

## Flows

### Post Flow
1. Send Instagram URL → `/add`
2. `/next_post` → download → analyze → plan → prompt → generate → preview
3. Approve all / reject / regenerate individual slides
4. Caption & hashtags generated → approve text → done

### Story Flow
1. `/next_story [theme]` → plan → prompt → generate → preview
2. Approve / reject / regenerate individual frames
3. Story text overlay suggestions → approve → done

### Reel Flow (Two-Stage)
1. Send reel URL → `/add`
2. `/next_reel` → download → analyze → generate **start frame only**
3. Approve start frame → **then** Kling renders video
4. Review final reel → approve → caption → done

## Project Structure

```
content_module/
├── __init__.py
├── __main__.py
├── mc_conductor.py          # Main orchestrator
├── core/
│   ├── config.py            # Central configuration
│   ├── state_manager.py     # Job CRUD, versioning, locking
│   ├── queue_manager.py     # Reference URL queue
│   └── asset_router.py      # Generator routing
├── generators/
│   ├── z_image_generator.py # Character images (ComfyUI)
│   ├── nano_banana_generator.py  # World images (Evolink)
│   ├── kling_generator.py   # Reel videos (Evolink)
│   └── instagram_scraper.py # Download references
├── llm_agents/
│   ├── reference_analyst.py # Analyze references
│   ├── planner.py           # Build content plans
│   ├── creative.py          # Write prompts
│   └── publish.py           # Captions & hashtags
└── telegram/
    └── telegram_manager.py  # Telegram communication
```

## Key Rules

- **Z-Image prompts** always start with `A w1man,` and include female descriptors
- **Kling** only starts after start frame is approved by user
- **MC Conductor** is the only module that talks to Telegram
- Each content type is a **separate job** (not bundled)
- State is file-based JSON with file locking
