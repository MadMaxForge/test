#!/usr/bin/env python3
"""
Content Scheduler - Manages content generation schedule for @lanna.danger.

Schedule (configurable):
  - Feed posts:  3 per week (Mon, Wed, Fri)
  - Stories:     1-3 per day
  - Reels:       3 per week (Tue, Thu, Sat)

Modes:
  --plan         Show today's content plan
  --generate     Generate content for today's plan
  --status       Show generation status and queue
  --cron-setup   Install cron jobs for automatic scheduling

Usage:
  python3 content_scheduler.py --plan
  python3 content_scheduler.py --generate
  python3 content_scheduler.py --status
"""

import os
import sys
import json
import subprocess
import random
from datetime import datetime, timezone, timedelta

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")

# Default schedule configuration
SCHEDULE = {
    "feed": {
        "days": [0, 2, 4],          # Mon, Wed, Fri
        "count": 1,                   # 1 carousel per scheduled day
        "carousel_size": 4,           # 4 images per carousel
        "time_slots": ["10:00", "14:00", "18:00"],
    },
    "story": {
        "days": [0, 1, 2, 3, 4, 5, 6],  # Every day
        "count_range": [1, 3],            # 1-3 stories per day
        "time_slots": ["09:00", "13:00", "17:00", "20:00"],
    },
    "reel": {
        "days": [1, 3, 5],          # Tue, Thu, Sat
        "count": 1,                   # 1 reel per scheduled day
        "time_slots": ["12:00", "16:00", "19:00"],
    },
}


def get_todays_plan():
    """Determine what content needs to be created today."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 6=Sun

    plan = []

    for content_type, config in SCHEDULE.items():
        if weekday in config["days"]:
            if content_type == "story":
                count = random.randint(*config["count_range"])
            else:
                count = config.get("count", 1)

            time_slots = config["time_slots"][:count]

            for i, slot in enumerate(time_slots):
                plan.append({
                    "content_type": content_type,
                    "slot": slot,
                    "index": i + 1,
                    "total": count,
                })

    return plan


def get_next_account_to_parse():
    """Get the next account to use as inspiration from memory."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from agent_memory import AgentMemory
        mem = AgentMemory()
        accounts = mem.get_accounts_to_parse(limit=3)
        mem.close()
        if accounts:
            return accounts[0]["username"]
    except Exception as e:
        print("[Scheduler] Warning: Could not query memory: %s" % e)

    # Fallback
    return "kyliejenner"


def run_pipeline(content_type, source_username, skip_generate=False, skip_telegram=False):
    """Run the content generation pipeline for one piece of content."""
    cmd = [
        "python3", os.path.join(SCRIPTS_DIR, "pipeline_runner.py"),
        source_username,
        "--content-type", content_type,
        "--count", "1",
    ]

    if skip_generate:
        cmd.append("--skip-generate")
    if skip_telegram:
        cmd.append("--skip-telegram")

    print("[Scheduler] Running: %s" % " ".join(cmd))

    env = os.environ.copy()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          timeout=600, cwd=WORKSPACE)

    if result.returncode != 0:
        print("[Scheduler] Pipeline failed (exit %d)" % result.returncode)
        print("[Scheduler] STDERR: %s" % result.stderr[-500:] if result.stderr else "")
        return False

    print("[Scheduler] Pipeline completed successfully")
    return True


def show_status():
    """Show current generation status."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from agent_memory import AgentMemory
        mem = AgentMemory()

        stats = mem.get_generation_stats()
        if stats:
            print("=== Generation Statistics ===")
            for s in stats:
                avg = float(s["avg_qc_score"]) if s["avg_qc_score"] else 0
                print("  %s: %s total, %s approved, %s rejected, avg QC: %.1f" % (
                    s["content_type"], s["total"], s["approved_count"],
                    s["rejected_count"], avg))
        else:
            print("No generation history yet.")

        print()

        lessons = mem.get_lessons(limit=5)
        if lessons:
            print("=== Recent Agent Activity ===")
            for l in lessons:
                print("  [%s] %s" % (
                    l.get("agent_name", "?"),
                    l.get("lesson_learned", "N/A")[:100],
                ))

        print()

        accounts = mem.get_accounts_to_parse(limit=5)
        if accounts:
            print("=== Next Accounts to Parse ===")
            for a in accounts:
                print("  @%s (score: %.1f, last: %s)" % (
                    a["username"],
                    float(a.get("compatibility_score", 0)),
                    a.get("last_parsed_at") or "never",
                ))

        mem.close()
    except Exception as e:
        print("[Scheduler] Error: %s" % e)


def setup_cron():
    """Install cron jobs for automatic content generation."""
    cron_lines = [
        "# Instagram AI Pipeline - Content Scheduler",
        "# Feed posts: Mon, Wed, Fri at 9:00 UTC",
        "0 9 * * 1,3,5 cd %s && export $(cat .env | grep -v '^#' | xargs) && python3 scripts/content_scheduler.py --generate >> /var/log/pipeline_scheduler.log 2>&1" % WORKSPACE,
        "# Reels: Tue, Thu, Sat at 11:00 UTC",
        "0 11 * * 2,4,6 cd %s && export $(cat .env | grep -v '^#' | xargs) && python3 scripts/content_scheduler.py --generate >> /var/log/pipeline_scheduler.log 2>&1" % WORKSPACE,
        "# Stories: Every day at 8:00, 13:00, 17:00 UTC",
        "0 8,13,17 * * * cd %s && export $(cat .env | grep -v '^#' | xargs) && python3 scripts/content_scheduler.py --generate >> /var/log/pipeline_scheduler.log 2>&1" % WORKSPACE,
        "# Account discovery: Every Sunday at 3:00 UTC",
        "0 3 * * 0 cd %s && export $(cat .env | grep -v '^#' | xargs) && python3 scripts/account_discovery.py --discover >> /var/log/pipeline_discovery.log 2>&1" % WORKSPACE,
    ]

    print("=== Cron Jobs to Install ===")
    print()
    for line in cron_lines:
        print(line)
    print()
    print("To install, run:")
    print("  (crontab -l 2>/dev/null; echo '') | cat - <(echo '%s') | crontab -" %
          "\\n".join(cron_lines))
    print()
    print("Or manually: crontab -e and paste the lines above.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 content_scheduler.py <command>")
        print("  --plan         Show today's content plan")
        print("  --generate     Generate content for today's plan")
        print("  --status       Show generation status and queue")
        print("  --cron-setup   Show cron job configuration")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "--plan":
        plan = get_todays_plan()
        now = datetime.now(timezone.utc)
        print("=== Content Plan for %s (%s) ===" % (
            now.strftime("%Y-%m-%d"), now.strftime("%A")))
        if not plan:
            print("  No content scheduled for today.")
        else:
            for item in plan:
                print("  [%s] %s #%d/%d at %s UTC" % (
                    item["content_type"].upper(),
                    item["content_type"],
                    item["index"],
                    item["total"],
                    item["slot"],
                ))
        print()
        source = get_next_account_to_parse()
        print("Next inspiration source: @%s" % source)

    elif cmd == "--generate":
        plan = get_todays_plan()
        if not plan:
            print("[Scheduler] No content scheduled for today.")
            return

        source = get_next_account_to_parse()
        print("[Scheduler] Today's plan: %d items, inspiration from @%s" % (len(plan), source))

        skip_gen = "--skip-generate" in sys.argv
        skip_tg = "--skip-telegram" in sys.argv

        for item in plan:
            print("\n" + "=" * 50)
            print("[Scheduler] Generating %s #%d (slot %s)" % (
                item["content_type"], item["index"], item["slot"]))
            print("=" * 50)

            success = run_pipeline(
                content_type=item["content_type"],
                source_username=source,
                skip_generate=skip_gen,
                skip_telegram=skip_tg,
            )

            if not success:
                print("[Scheduler] WARNING: Failed to generate %s #%d" % (
                    item["content_type"], item["index"]))

        print("\n[Scheduler] Done! Generated %d content items." % len(plan))

    elif cmd == "--status":
        show_status()

    elif cmd == "--cron-setup":
        setup_cron()

    else:
        print("Unknown command: %s" % cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
