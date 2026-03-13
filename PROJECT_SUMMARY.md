# Instagram AI Pipeline — Project Summary

## Overview
Automated Instagram content generation pipeline. One reference URL produces a full content package: carousel posts + stories + optional reel. Managed via Telegram bot with approve/reject/regenerate workflow.

## Architecture

```
Instagram URL
    |
    v
MC Conductor (Telegram bot, main orchestrator)
    |
    v
Reference Queue --> Scout Agent (vision analysis)
    |
    v
Package Planner (composition: N posts, N stories, 0-1 reel, ~65% character ratio)
    |
    v
Package Creative (prompts via kimi-k2.5)
    |
    v
Asset Router --> RunPod Z-Image (character) OR Nano Banana (world)
    |               + Kling Motion Control (reel video)
    v
QC Agent (vision quality check)
    |
    v
Telegram Manager (preview + approve/reject/regenerate buttons)
    |
    v
Package Publish (captions + 5 hashtags via gemini-flash-lite)
    |
    v
Text Review in Telegram --> Approved --> Scheduled/Published
```

## VPS Server
- **Host**: Set via `NEW_VPS_HOST` secret (do not commit IP to repo)
- **SSH Port**: 2222 (not default 22!)
- **User**: root
- **Command**: `ssh -p 2222 root@$NEW_VPS_HOST`
- **Workspace**: `/root/.openclaw/workspace/`
- **Scripts**: `/root/.openclaw/workspace/scripts/`
- **Env vars**: `/root/.openclaw/workspace/.env`

## Environment Variables (.env)
```
OPENROUTER_API_KEY=sk-or-v1-...    # LLM for all agents
TELEGRAM_BOT_TOKEN=...              # Telegram bot
TELEGRAM_CHAT_ID=...                # Your Telegram chat ID
EVOLINK_API_KEY=sk-...              # Nano Banana 2 + Kling Motion Control
RUNPOD_API_KEY=rpa_...              # Z-Image Turbo (RunPod serverless)
RUNPOD_ENDPOINT=...                 # RunPod endpoint ID
OPENCLAW_WORKSPACE=/root/.openclaw/workspace
```

## LLM Models (LOCKED)
| Agent | Model | Why |
|---|---|---|
| Scout, QC, Publish | `google/gemini-2.0-flash-lite-001` | Cost optimization, fast |
| Creative | `moonshotai/kimi-k2.5` | Best prompt quality |

## Image Generators
| Generator | Use Case | API |
|---|---|---|
| Z-Image Turbo | Character images (has_character=true) | RunPod serverless |
| Nano Banana 2 | World/environment images (has_character=false) | Evolink API |
| Kling Motion Control | Reel video from start_frame + motion ref | Evolink API |

## File Structure

### Package System (core)
| File | Purpose |
|---|---|
| `scripts/mc_conductor.py` | Main orchestrator. Telegram event loop, commands (/add, /next, /status, /queue). Delegates to all modules |
| `scripts/state_manager.py` | Package CRUD, asset versioning, job locking, rollback, recovery |
| `scripts/asset_router.py` | Deterministic routing: has_character=true --> z_image, else --> nano_banana |
| `scripts/reference_queue.py` | Two queues (post_references + reel_references), lifecycle: new --> used --> archived |
| `scripts/package_planner.py` | Decides composition: N post slides, N story frames, 0-1 reel. Targets ~65% character ratio |
| `scripts/package_creative.py` | Generates image prompts per asset. Z-Image: "A w1man, " prefix. Nano Banana: <=150 chars + "real photo" |
| `scripts/package_publish.py` | Generates captions (100-150 chars), exactly 5 hashtags, story overlays (<=4 words), reel caption |
| `scripts/telegram_manager.py` | Telegram UI: package preview, text preview, reel frame approval, per-asset regeneration buttons |

### Agents
| File | Purpose |
|---|---|
| `scripts/scout_agent.py` | Vision analysis of Instagram references (6 elements per image) |
| `scripts/creative_agent.py` | Legacy prompt generator (backup, functions moved to package_creative.py) |
| `scripts/publish_agent.py` | Legacy text generator (backup, functions moved to package_publish.py) |
| `scripts/qc_agent.py` | Vision-based quality scoring of generated images |

### Generators
| File | Purpose |
|---|---|
| `scripts/runpod_generator.py` | Z-Image Turbo via RunPod serverless API |
| `scripts/nano_banana_generator.py` | Nano Banana 2 via Evolink API |
| `scripts/kling_motion_control.py` | Kling video generation via Evolink API |
| `scripts/instagram_scraper.py` | Instagram profile scraping + image download |

### Utilities
| File | Purpose |
|---|---|
| `scripts/agent_memory.py` | SQLite-based memory for patterns, generations, events |

## Package State Machine
```
draft --> planning --> production --> review --> text_review --> approved --> scheduled --> published
```

## Key Rules
- **Max per package**: 4 posts, 4 stories, 1 reel (9 total assets)
- **Character/world ratio**: Target 60-70% character (Z-Image), 30-40% world (Nano Banana)
- **Z-Image prompts**: MUST start with "A w1man, "
- **Nano Banana prompts**: Max 150 chars, MUST include "real photo"
- **Hashtags**: Exactly 5 per post
- **Caption**: 100-150 characters
- **Story overlays**: Max 4 words or null
- **Reel two-stage**: Approve start_frame BEFORE launching expensive Kling render
- **Job locking**: Only 1 active job per package (auto-expire after timeout)
- **Recovery**: Restart-safe, resume from last known state

## Telegram Commands
| Command | Action |
|---|---|
| `/add <url>` | Add Instagram URL to reference queue |
| `/next` | Process next reference from queue |
| `/status` | Show current package status |
| `/queue` | Show reference queue |

## Telegram Callback Buttons
- `pkg_approve_<id>` — Approve package visuals
- `pkg_reject_<id>` — Reject, request regeneration
- `txt_approve_<id>` — Approve text (captions + hashtags)
- `txt_reject_<id>` — Reject text
- `regen_asset_<id>___<asset_id>` — Regenerate specific asset
- `reel_approve_<id>` — Approve reel start frame
- `reel_reject_<id>` — Reject reel start frame

## Workspace Directory Structure
```
/root/.openclaw/workspace/
  .env                          # API keys
  reference_queue.json          # Queue of Instagram URLs
  scripts/                      # All Python modules
  packages/
    <package_id>/
      references/               # Downloaded reference images
      analysis/                 # Scout analysis JSON
      plan/                     # Package plan JSON
      prompts/                  # Creative prompts JSON
      generated/
        posts/                  # Generated post images
        stories/                # Generated story images
        reels/                  # Generated reel frames/video
      text/                     # Publish text JSON
      versions/                 # Asset version history
```

## GitHub
- **Repo**: MadMaxForge/test
- **Unified PR**: #9 (includes everything)

## Phase 2 (not yet implemented)
- Engagement Agent (instagrapi + proxy + rate limiter)
- Multi-account rotation
- Auto-parser + ranking/recommendation from reference archive
- Cron schedule for auto-posting
