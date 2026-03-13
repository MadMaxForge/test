# AGENTS.md — Instagram AI Pipeline Team

## Pipeline Overview
```
Scrape → Scout → Director → Creative → RunPod Z-Image / Nano Banana 2 → Kling MC (reels) → QC → Publish → Telegram Preview → Human Approval
```

## Agent Roster

### Lead Agent :gear: (Board Lead)
- **Role:** Pipeline orchestrator
- **Task:** Coordinate task flow between all agents, track pipeline progress
- **Tool:** `python3 scripts/pipeline_runner.py <username> [--content-type TYPE]`

### Scout :mag: (Instagram Profile Parser)
- **Role:** Download and analyze reference Instagram profiles
- **Task:** Parse posts, analyze images with vision, create structured JSON reports
- **Tool:** `python3 scripts/scout_agent.py <username>`
- **Output:** `scout_analysis/{username}_{date}.json`

### Director :art: (Creative Director)
- **Role:** Transform Scout analysis into technical generation briefs
- **Task:** Specify exact prompts, LoRA models, dimensions, styles
- **Tool:** `python3 scripts/director_agent.py <username>`
- **Output:** `director_briefs/brief_{date}_{N}.json`

### Creative :paintbrush: (Prompt Engineer)
- **Role:** Generate detailed Z-Image prompts for photorealistic content
- **Task:** Create structured prompts following w1man LoRA template
- **Tool:** `python3 scripts/creative_agent.py <username>`
- **Output:** `creative_prompts/{username}_prompts_{date}.json`
- **Special:** For reels — camera distance MUST match reference video

### QC :mag_right: (Quality Control)
- **Role:** Validate generated image quality before publishing
- **Task:** Score on 5 criteria (0-10), detect artifacts, approve/reject
- **Tool:** `python3 scripts/qc_agent.py <username> [--threshold N]`
- **Output:** `qc_reports/{username}_qc_{date}.json`
- **Threshold:** >= 7.0 average to pass

### Publish :outbox_tray: (Publisher)
- **Role:** Assemble posts and send Telegram previews for human approval
- **Task:** Create carousel with captions/hashtags, get human approval via Telegram
- **Tools:**
  - `python3 scripts/publish_agent.py <username>`
  - `python3 scripts/telegram_preview.py <username>`
- **Rule:** NEVER publish without human Telegram approval

## Content Types
| Type | Dimensions | Aspect Ratio | Use Case |
|------|-----------|--------------|----------|
| feed | 1080x1350 | 4:5 | Carousel/feed posts |
| story | 1088x1920 | 9:16 | Instagram stories |
| reel | 1088x1920 | 9:16 | Reels (+ Kling Motion Control) |
| square | 1024x1024 | 1:1 | Square posts |

## Generation Tools
| Tool | Purpose | API |
|------|---------|-----|
| RunPod Z-Image | Character images (w1man LoRA) | RunPod Serverless |
| Nano Banana 2 | Non-character images (backgrounds, products) | Evolink API |
| Kling Motion Control | Reel video generation | Evolink API |

## Reel Generation Workflow (Kling + Z-Image)
1. Scout analyzes reference video first frame (camera distance, angle, pose)
2. Creative generates prompt matching reference framing
3. RunPod Z-Image generates character image matching reference
4. Kling Motion Control transfers motion from reference video to character
5. **CRITICAL:** Camera distance mismatch = broken motion transfer

## Content Schedule (Target)
- Feed posts: 3/week (Mon, Wed, Fri)
- Stories: 1-3/day
- Reels: 3/week (Tue, Thu, Sat)
