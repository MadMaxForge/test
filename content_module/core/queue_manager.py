"""
Queue Manager — reference queue with statuses (new / used / rejected).

Stores references as JSON files in QUEUE_DIR.
Two separate queues: posts and reels.

This is a pure Python module — no LLM calls.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from content_module.core.config import QUEUE_DIR


QUEUE_FILE = QUEUE_DIR / "references.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _save_queue(queue: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
    tmp.rename(QUEUE_FILE)


def add_reference(url: str, ref_type: str = "post") -> dict:
    """
    Add a reference URL to the queue.

    Args:
        url: Instagram URL (post, carousel, or reel)
        ref_type: 'post' or 'reel'

    Returns:
        The created reference entry.
    """
    if ref_type not in ("post", "reel"):
        raise ValueError(f"Invalid ref_type: {ref_type}. Must be 'post' or 'reel'")

    queue = _load_queue()

    # Check for duplicates
    for entry in queue:
        if entry["url"] == url:
            return entry

    ref_id = f"ref_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    entry = {
        "ref_id": ref_id,
        "url": url,
        "type": ref_type,
        "status": "new",
        "added_at": _now_iso(),
        "used_at": None,
        "job_id": None,
    }

    queue.append(entry)
    _save_queue(queue)
    return entry


def get_next(ref_type: Optional[str] = None) -> Optional[dict]:
    """
    Get the next unused reference from the queue.

    Args:
        ref_type: Filter by type ('post' or 'reel'). None = any.

    Returns:
        The next 'new' reference, or None if queue is empty.
    """
    queue = _load_queue()
    for entry in queue:
        if entry["status"] != "new":
            continue
        if ref_type and entry["type"] != ref_type:
            continue
        return entry
    return None


def mark_used(ref_id: str, job_id: str) -> Optional[dict]:
    """Mark a reference as used and link it to a job."""
    queue = _load_queue()
    for entry in queue:
        if entry["ref_id"] == ref_id:
            entry["status"] = "used"
            entry["used_at"] = _now_iso()
            entry["job_id"] = job_id
            _save_queue(queue)
            return entry
    return None


def mark_rejected(ref_id: str) -> Optional[dict]:
    """Mark a reference as rejected."""
    queue = _load_queue()
    for entry in queue:
        if entry["ref_id"] == ref_id:
            entry["status"] = "rejected"
            _save_queue(queue)
            return entry
    return None


def list_references(
    status_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
) -> list[dict]:
    """List all references, optionally filtered."""
    queue = _load_queue()
    results = queue
    if status_filter:
        results = [r for r in results if r["status"] == status_filter]
    if type_filter:
        results = [r for r in results if r["type"] == type_filter]
    return results


def get_queue_stats() -> dict:
    """Get summary statistics of the queue."""
    queue = _load_queue()
    stats = {
        "total": len(queue),
        "new_posts": sum(1 for r in queue if r["status"] == "new" and r["type"] == "post"),
        "new_reels": sum(1 for r in queue if r["status"] == "new" and r["type"] == "reel"),
        "used": sum(1 for r in queue if r["status"] == "used"),
        "rejected": sum(1 for r in queue if r["status"] == "rejected"),
    }
    return stats


def detect_reference_type(url: str) -> str:
    """Auto-detect if a URL is a post or reel reference."""
    url_lower = url.lower()
    if "/reel/" in url_lower or "/reels/" in url_lower:
        return "reel"
    return "post"
