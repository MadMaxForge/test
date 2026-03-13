# Instagram AI Content Pipeline

Automated Instagram content generation via Telegram bot. One reference URL produces a full content package: carousel posts + stories + optional reel.

## Quick Start

```bash
# 1. SSH to VPS (IP stored in NEW_VPS_HOST secret, not committed to repo)
ssh -p 2222 root@$NEW_VPS_HOST

# 2. Set env vars
export $(grep -v '^#' /root/.openclaw/workspace/.env | xargs)
export OPENCLAW_WORKSPACE=/root/.openclaw/workspace

# 3. Start MC Conductor (Telegram bot)
cd /root/.openclaw/workspace/scripts
python3 mc_conductor.py

# 4. In Telegram:
#    /add https://instagram.com/p/ABC123/   -- add reference
#    /next                                   -- process next
#    /status                                 -- current package
#    /queue                                  -- reference queue
```

## Architecture

See [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) for full details.

```
Reference URL --> Scout --> Planner --> Creative --> Generators --> QC --> Telegram Preview --> Publish
```

## Required Environment Variables

```
OPENROUTER_API_KEY    # LLM (OpenRouter)
TELEGRAM_BOT_TOKEN    # Telegram bot
TELEGRAM_CHAT_ID      # Your chat ID
EVOLINK_API_KEY       # Nano Banana + Kling
RUNPOD_API_KEY        # Z-Image (RunPod)
OPENCLAW_WORKSPACE    # /root/.openclaw/workspace
```
