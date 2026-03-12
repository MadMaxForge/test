#!/usr/bin/env python3
"""
Account Discovery Agent - Finds female beauty/fashion/lifestyle accounts
for content inspiration and style learning.

Strategy:
  1. Start with seed accounts (kyliejenner, bellahadid, etc.)
  2. Parse their "suggested accounts" / tagged accounts
  3. Filter: female-presenting, beauty/fashion/lifestyle, 50K+ followers
  4. Score compatibility with w1man LoRA style
  5. Save to discovered_accounts table for Scout to parse

Usage:
  python3 account_discovery.py --discover          # Find new accounts from seeds
  python3 account_discovery.py --list              # List discovered accounts
  python3 account_discovery.py --next N            # Get next N accounts to parse
  python3 account_discovery.py --add @username      # Manually add account
"""

import os
import sys
import json
import re
import time
import requests
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("OPENROUTER_MODEL", "moonshotai/kimi-k2.5")

# Categories that are compatible with our w1man LoRA
COMPATIBLE_CATEGORIES = [
    "beauty", "fashion", "lifestyle", "glamour", "model",
    "influencer", "makeup", "skincare", "fitness",
]

# Minimum followers for discovery
MIN_FOLLOWERS = 50000


def call_openrouter(prompt, system_prompt="You are an Instagram account analyst.", max_tokens=4000):
    """Call OpenRouter API for account analysis."""
    if not OPENROUTER_API_KEY:
        print("[Discovery] ERROR: OPENROUTER_API_KEY not set")
        return None

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        print("[Discovery] API error %d: %s" % (resp.status_code, resp.text[:300]))
        return None

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return None
    return choices[0].get("message", {}).get("content")


def parse_json_response(text):
    """Parse JSON from LLM response."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find JSON array or object
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        start = text.find(start_char)
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return None


def discover_similar_accounts(seed_accounts, existing_usernames=None):
    """Use LLM to suggest similar female beauty/fashion accounts.

    This doesn't scrape Instagram — it uses LLM knowledge to suggest
    accounts similar to our seeds that would be good for style learning.
    """
    if existing_usernames is None:
        existing_usernames = set()

    seed_list = ", ".join(["@" + u for u in seed_accounts[:10]])

    prompt = """Based on these Instagram seed accounts: %s

Suggest 15-20 similar Instagram accounts that are:
1. Female-presenting beauty/fashion/lifestyle influencers
2. Known for high-quality, editorial-style photography
3. Active with regular posts (not inactive accounts)
4. Minimum ~50K followers
5. Visual style compatible with glamour/beauty photography
6. NOT already in this list: %s

For each account, provide:
- username (exact Instagram handle, no @)
- estimated_followers (number)
- category (beauty/fashion/lifestyle/model/glamour)
- style_description (brief, 10-20 words)
- compatibility_score (0-10, how well their visual style matches luxury beauty aesthetic)

Output ONLY a JSON array of objects. No markdown, no explanations.
Example: [{"username": "example", "estimated_followers": 500000, "category": "beauty", "style_description": "warm tones editorial glamour", "compatibility_score": 8.5}]""" % (seed_list, ", ".join(["@" + u for u in existing_usernames]))

    print("[Discovery] Asking LLM for similar accounts to %s..." % seed_list)
    response = call_openrouter(prompt, max_tokens=4000)
    if not response:
        print("[Discovery] ERROR: No response from LLM")
        return []

    accounts = parse_json_response(response)
    if not accounts or not isinstance(accounts, list):
        print("[Discovery] ERROR: Could not parse account suggestions")
        return []

    # Filter and validate
    valid = []
    for acc in accounts:
        username = acc.get("username", "").strip().lower().replace("@", "")
        if not username or username in existing_usernames:
            continue
        followers = int(acc.get("estimated_followers", 0))
        category = acc.get("category", "beauty").lower()
        score = float(acc.get("compatibility_score", 5.0))
        style = acc.get("style_description", "")

        # Compatibility filter
        if score < 6.0:
            continue
        if category not in COMPATIBLE_CATEGORIES:
            continue

        valid.append({
            "username": username,
            "followers": followers,
            "category": category,
            "compatibility_score": score,
            "style_description": style,
        })

    print("[Discovery] Found %d valid accounts (from %d suggestions)" % (len(valid), len(accounts)))
    return valid


def evaluate_account_from_analysis(analysis_json):
    """Evaluate an already-analyzed account for w1man LoRA compatibility.

    Takes a Scout analysis JSON and returns a compatibility score.
    """
    prompt = """Analyze this Instagram account analysis for compatibility with a female beauty/glamour AI model (LoRA-based image generation).

Account analysis:
%s

Rate the compatibility on these criteria (each 0-10):
1. Visual style match (warm tones, editorial, glamour)
2. Photo quality (high-res, professional lighting)
3. Pose variety (different poses that can inspire AI generation)
4. Setting variety (different backgrounds/locations)
5. Outfit variety (fashion diversity)

Output JSON only:
{"overall_score": 8.5, "visual_style": 9, "photo_quality": 8, "pose_variety": 8, "setting_variety": 9, "outfit_variety": 8, "recommendation": "Great source for glamour editorial style", "best_elements": ["warm lighting", "luxury settings"]}""" % json.dumps(analysis_json, indent=2)[:3000]

    response = call_openrouter(prompt, max_tokens=1000)
    return parse_json_response(response)


def main():
    # Import memory (on server)
    sys.path.insert(0, os.path.join(WORKSPACE, "scripts"))
    try:
        from agent_memory import AgentMemory
        mem = AgentMemory()
    except ImportError:
        print("[Discovery] ERROR: agent_memory.py not found. Run from workspace/scripts/")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python3 account_discovery.py <command>")
        print("  --discover     Find new accounts from seeds")
        print("  --list         List all discovered accounts")
        print("  --next N       Get next N accounts to parse")
        print("  --add @user    Manually add an account")
        print("  --evaluate     Re-evaluate accounts based on existing analyses")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "--discover":
        # Get existing accounts
        existing = mem.get_accounts_to_parse(limit=100)
        existing_usernames = set(a["username"] for a in existing)

        # Get seed accounts (highest compatibility)
        seeds = [a["username"] for a in existing if float(a.get("compatibility_score", 0)) >= 8.0]
        if not seeds:
            seeds = ["kyliejenner", "bellahadid", "haileybieber", "zendaya"]

        new_accounts = discover_similar_accounts(seeds, existing_usernames)

        saved = 0
        for acc in new_accounts:
            mem.save_discovered_account(
                acc["username"],
                source="llm_discovery",
                followers=acc["followers"],
                category=acc["category"],
                compatibility_score=acc["compatibility_score"],
            )
            print("  [+] @%s (%.1f score, %s, %dK followers)" % (
                acc["username"], acc["compatibility_score"],
                acc["category"], acc["followers"] // 1000))
            saved += 1

        mem.log_event("lead", "account_discovery", {
            "new_accounts": saved,
            "total_suggestions": len(new_accounts),
            "seeds_used": seeds,
        }, lesson="Discovered %d new accounts from %d seeds" % (saved, len(seeds)))

        print("\n[Discovery] Saved %d new accounts" % saved)

    elif cmd == "--list":
        accounts = mem.get_accounts_to_parse(limit=50)
        if not accounts:
            print("No discovered accounts. Run --discover first.")
        else:
            print("=== Discovered Accounts (%d) ===" % len(accounts))
            for a in accounts:
                parsed = a.get("last_parsed_at")
                status = "parsed: %s" % parsed if parsed else "never parsed"
                print("  @%-20s  score: %.1f  category: %-10s  followers: %s  (%s)" % (
                    a["username"],
                    float(a.get("compatibility_score", 0)),
                    a.get("category", "?"),
                    a.get("followers", "?"),
                    status,
                ))

    elif cmd == "--next":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        accounts = mem.get_accounts_to_parse(limit=n)
        if not accounts:
            print("No accounts to parse. Run --discover first.")
        else:
            print("Next %d accounts to parse:" % len(accounts))
            for a in accounts:
                print("  @%s (score: %.1f, category: %s)" % (
                    a["username"],
                    float(a.get("compatibility_score", 0)),
                    a.get("category", "?"),
                ))

    elif cmd == "--add":
        if len(sys.argv) < 3:
            print("Usage: --add @username [category] [score]")
            sys.exit(1)
        username = sys.argv[2].strip().lower().replace("@", "")
        category = sys.argv[3] if len(sys.argv) > 3 else "beauty"
        score = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0
        mem.save_discovered_account(username, source="manual", category=category,
                                     compatibility_score=score)
        print("[Discovery] Added @%s (category: %s, score: %.1f)" % (username, category, score))

    elif cmd == "--evaluate":
        # Re-evaluate accounts based on existing Scout analyses
        analysis_dir = os.path.join(WORKSPACE, "scout_analysis")
        if not os.path.exists(analysis_dir):
            print("No analyses found in %s" % analysis_dir)
            sys.exit(1)

        for fname in os.listdir(analysis_dir):
            if not fname.endswith("_analysis.json"):
                continue
            username = fname.replace("_analysis.json", "")
            fpath = os.path.join(analysis_dir, fname)
            with open(fpath) as f:
                analysis = json.load(f)

            print("[Evaluating] @%s..." % username)
            eval_result = evaluate_account_from_analysis(analysis)
            if eval_result and "overall_score" in eval_result:
                score = float(eval_result["overall_score"])
                mem.save_discovered_account(username, source="evaluation",
                                             compatibility_score=score)
                mem.log_event("lead", "account_evaluation", {
                    "username": username,
                    "score": score,
                    "details": eval_result,
                }, lesson="@%s scored %.1f for w1man compatibility" % (username, score))
                print("  Score: %.1f - %s" % (score, eval_result.get("recommendation", "")))

    mem.close()


if __name__ == "__main__":
    main()
