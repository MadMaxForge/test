"""Rate Limiter — prevents excessive posting and tracks cooldowns.

Simple file-based rate limiter that tracks:
  - Posts per day (max configurable, default 2)
  - Cooldown between actions (configurable hours)
  - Last action timestamps
"""

import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Default state file location
_STATE_FILE = os.path.join(os.path.dirname(__file__), "config", ".rate_limiter_state.json")


def _load_state() -> dict:
    """Load rate limiter state from disk."""
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"posts": [], "last_action": None}


def _save_state(state: dict) -> None:
    """Save rate limiter state to disk."""
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        logger.error(f"Failed to save rate limiter state: {e}")


def can_post(
    max_posts_per_day: int = 2,
    cooldown_hours: float = 6.0,
) -> dict:
    """Check if posting is allowed right now.

    Args:
        max_posts_per_day: Maximum posts allowed per 24h period.
        cooldown_hours: Minimum hours between posts.

    Returns:
        dict with 'allowed' (bool), 'reason' (str), 'next_allowed_at' (ISO str or None).
    """
    state = _load_state()
    now = datetime.now(timezone.utc)

    # Filter posts to last 24 hours
    cutoff = now - timedelta(hours=24)
    recent_posts = []
    for post_time_str in state.get("posts", []):
        try:
            post_time = datetime.fromisoformat(post_time_str)
            if post_time > cutoff:
                recent_posts.append(post_time)
        except (ValueError, TypeError):
            continue

    # Check daily limit
    if len(recent_posts) >= max_posts_per_day:
        oldest = min(recent_posts)
        next_allowed = oldest + timedelta(hours=24)
        return {
            "allowed": False,
            "reason": f"Daily limit reached ({len(recent_posts)}/{max_posts_per_day}). Wait until oldest post expires.",
            "next_allowed_at": next_allowed.isoformat(),
            "posts_today": len(recent_posts),
        }

    # Check cooldown
    last_action = state.get("last_action")
    if last_action:
        try:
            last_time = datetime.fromisoformat(last_action)
            cooldown_end = last_time + timedelta(hours=cooldown_hours)
            if now < cooldown_end:
                remaining = cooldown_end - now
                return {
                    "allowed": False,
                    "reason": f"Cooldown active. {remaining.seconds // 3600}h {(remaining.seconds % 3600) // 60}m remaining.",
                    "next_allowed_at": cooldown_end.isoformat(),
                    "posts_today": len(recent_posts),
                }
        except (ValueError, TypeError):
            pass

    return {
        "allowed": True,
        "reason": f"OK. {len(recent_posts)}/{max_posts_per_day} posts in last 24h.",
        "next_allowed_at": None,
        "posts_today": len(recent_posts),
    }


def record_post() -> None:
    """Record that a post was made right now."""
    state = _load_state()
    now = datetime.now(timezone.utc)

    state.setdefault("posts", [])
    state["posts"].append(now.isoformat())
    state["last_action"] = now.isoformat()

    # Clean up old entries (older than 48h)
    cutoff = now - timedelta(hours=48)
    state["posts"] = [
        p for p in state["posts"]
        if _parse_time(p) and _parse_time(p) > cutoff  # type: ignore[operator]
    ]

    _save_state(state)
    logger.info(f"Post recorded at {now.isoformat()}")


def record_action() -> None:
    """Record any action (for cooldown tracking without counting as a post)."""
    state = _load_state()
    state["last_action"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)


def reset_state() -> None:
    """Reset the rate limiter state (for testing)."""
    _save_state({"posts": [], "last_action": None})
    logger.info("Rate limiter state reset")


def get_status(max_posts_per_day: int = 2) -> dict:
    """Get current rate limiter status."""
    state = _load_state()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    recent_posts = [
        p for p in state.get("posts", [])
        if _parse_time(p) and _parse_time(p) > cutoff  # type: ignore[operator]
    ]

    return {
        "posts_today": len(recent_posts),
        "max_posts_per_day": max_posts_per_day,
        "remaining": max(0, max_posts_per_day - len(recent_posts)),
        "last_action": state.get("last_action"),
    }


def _parse_time(time_str: str) -> Optional[datetime]:
    """Safely parse an ISO datetime string."""
    try:
        return datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return None
