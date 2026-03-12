#!/usr/bin/env python3
"""
Director Agent — reads Scout analysis and creates a concise brief.
Uses OpenRouter API (Kimi K2.5) for AI summarization.

Usage: python3 director_agent.py <username>
"""

import json
import os
import re
import sys
import requests

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "moonshotai/kimi-k2.5"

DIRECTOR_SYSTEM_PROMPT = """You are Director — a strategic brief creator for Instagram content planning.
You receive a Scout analysis of an Instagram profile and create an actionable brief.

Output ONLY a valid JSON object (no markdown, no code fences) with this structure:
{
  "username": "...",
  "executive_summary": "3-4 sentence strategic overview",
  "target_audience": {
    "demographics": "description",
    "interests": ["interest1", "interest2"],
    "pain_points": ["point1", "point2"]
  },
  "content_strategy": {
    "pillars": ["pillar1", "pillar2", "pillar3"],
    "posting_frequency": "recommended schedule",
    "best_times": "suggested posting times",
    "content_mix": {"photos": "X%", "carousels": "X%", "reels": "X%"}
  },
  "growth_tactics": ["tactic1", "tactic2", "tactic3"],
  "immediate_actions": ["action1", "action2", "action3"],
  "kpis": ["kpi1", "kpi2", "kpi3"],
  "competitive_edge": "what makes this profile unique"
}

IMPORTANT: Output ONLY the JSON. No text before or after. No markdown fences. Keep strings short."""


def parse_json_response(text):
    """Robustly parse JSON from LLM response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        break

    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        candidate = m.group(1)
        open_b = candidate.count('{') - candidate.count('}')
        open_a = candidate.count('[') - candidate.count(']')
        if open_b > 0 or open_a > 0:
            last_complete = max(candidate.rfind('",'), candidate.rfind('"],'), candidate.rfind('},'))
            if last_complete > 0:
                candidate = candidate[:last_complete+1]
            candidate += ']' * max(0, open_a) + '}' * max(0, open_b)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    print(f"[ERROR] Could not parse JSON from response:\n{text[:500]}")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 director_agent.py <username>")
        sys.exit(1)

    username = sys.argv[1]
    print(f"[Director] Creating brief for @{username}...")

    analysis_path = os.path.join(WORKSPACE, "scout_analysis", f"{username}_analysis.json")
    if not os.path.exists(analysis_path):
        print(f"[ERROR] Scout analysis not found: {analysis_path}")
        sys.exit(1)

    with open(analysis_path) as f:
        analysis = json.load(f)
    print(f"[Director] Loaded Scout analysis")

    prompt = f"""Based on this Scout analysis of @{username}, create a strategic brief.

{json.dumps(analysis, indent=2, ensure_ascii=False)}

Output ONLY the JSON brief. No markdown. Keep all string values concise (under 200 chars each)."""

    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY not set")
        sys.exit(1)

    print(f"[*] Calling {MODEL} via OpenRouter...")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 3000,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        print(f"[ERROR] API returned {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    brief = parse_json_response(content)

    output_dir = os.path.join(WORKSPACE, "director_briefs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{username}_brief.json")

    with open(output_path, "w") as f:
        json.dump(brief, f, indent=2, ensure_ascii=False)

    print(f"[Director] Brief saved: {output_path}")
    print(f"[Director] Summary: {brief.get('executive_summary', 'N/A')[:200]}")


if __name__ == "__main__":
    main()
