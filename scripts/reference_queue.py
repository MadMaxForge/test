#!/usr/bin/env python3
"""
Reference Queue - Manages the pool of reference links (posts + reels).

Two queues:
- post_references: links to Instagram posts/carousels for Scout analysis
- reel_references: links to Instagram reels for motion reference (Kling)

Lifecycle: new → used → archived
Used references track which package consumed them.

Usage:
    from reference_queue import ReferenceQueue
    rq = ReferenceQueue()
    rq.add_post_reference("https://instagram.com/p/ABC123", note="poolside vibes")
    ref = rq.get_next_post_reference()
    rq.mark_used(ref["ref_id"], "pkg_20260313_poolside_luxury")
"""

import json
import os
import fcntl
import re
from datetime import datetime, timezone


WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
QUEUE_FILE = os.path.join(WORKSPACE, "reference_queue.json")


class ReferenceQueue:
    """Manages reference queues for posts and reels."""

    def __init__(self, queue_file=None):
        self.queue_file = queue_file or QUEUE_FILE
        self._ensure_file()

    # ── Add References ───────────────────────────────────────────

    def add_post_reference(self, url, note=None, source_account=None):
        """
        Add a post/carousel reference to the queue.

        Args:
            url: str - Instagram post URL
            note: str | None - optional description
            source_account: str | None - e.g. "@kyliejenner"

        Returns:
            dict - the created reference entry
        """
        return self._add_reference("post_references", url, note, source_account)

    def add_reel_reference(self, url, note=None, source_account=None):
        """
        Add a reel reference to the queue (for Kling motion).

        Args:
            url: str - Instagram reel URL
            note: str | None - e.g. "dance by pool"
            source_account: str | None

        Returns:
            dict - the created reference entry
        """
        return self._add_reference("reel_references", url, note, source_account)

    # ── Get Next ─────────────────────────────────────────────────

    def get_next_post_reference(self):
        """
        Get the oldest 'new' post reference from the queue.

        Returns:
            dict | None - reference entry, or None if queue is empty
        """
        return self._get_next("post_references")

    def get_next_reel_reference(self):
        """
        Get the oldest 'new' reel reference from the queue.

        Returns:
            dict | None - reference entry, or None if queue is empty
        """
        return self._get_next("reel_references")

    # ── Mark Used / Archive ──────────────────────────────────────

    def mark_used(self, ref_id, package_id):
        """
        Mark a reference as used by a specific package.

        Args:
            ref_id: str - reference ID
            package_id: str - package that used this reference
        """
        data = self._read()

        for queue_key in ("post_references", "reel_references"):
            for ref in data[queue_key]:
                if ref["ref_id"] == ref_id:
                    ref["status"] = "used"
                    ref["used_in"] = package_id
                    ref["used_at"] = datetime.now(timezone.utc).isoformat()
                    self._write(data)
                    print("[ReferenceQueue] Marked %s as used in %s" % (ref_id, package_id))
                    return

        raise ValueError("Reference not found: %s" % ref_id)

    def archive(self, ref_id):
        """Move a reference to archived status."""
        data = self._read()

        for queue_key in ("post_references", "reel_references"):
            for ref in data[queue_key]:
                if ref["ref_id"] == ref_id:
                    ref["status"] = "archived"
                    ref["archived_at"] = datetime.now(timezone.utc).isoformat()
                    self._write(data)
                    print("[ReferenceQueue] Archived %s" % ref_id)
                    return

        raise ValueError("Reference not found: %s" % ref_id)

    # ── List / Count ─────────────────────────────────────────────

    def list_post_references(self, status=None):
        """List post references, optionally filtered by status."""
        return self._list("post_references", status)

    def list_reel_references(self, status=None):
        """List reel references, optionally filtered by status."""
        return self._list("reel_references", status)

    def count(self):
        """Get counts of references by type and status."""
        data = self._read()
        result = {}

        for queue_key in ("post_references", "reel_references"):
            counts = {"new": 0, "used": 0, "archived": 0}
            for ref in data[queue_key]:
                s = ref.get("status", "new")
                counts[s] = counts.get(s, 0) + 1
            result[queue_key] = counts

        return result

    # ── Remove ───────────────────────────────────────────────────

    def remove(self, ref_id):
        """Remove a reference entirely from the queue."""
        data = self._read()

        for queue_key in ("post_references", "reel_references"):
            original_len = len(data[queue_key])
            data[queue_key] = [r for r in data[queue_key] if r["ref_id"] != ref_id]
            if len(data[queue_key]) < original_len:
                self._write(data)
                print("[ReferenceQueue] Removed %s" % ref_id)
                return

        raise ValueError("Reference not found: %s" % ref_id)

    # ── Detect URL type ──────────────────────────────────────────

    @staticmethod
    def detect_url_type(url):
        """
        Detect if a URL is a post or reel.

        Args:
            url: str - Instagram URL

        Returns:
            str - "post" or "reel" or "unknown"
        """
        url_lower = url.lower()
        if "/reel/" in url_lower or "/reels/" in url_lower:
            return "reel"
        if "/p/" in url_lower or "/tv/" in url_lower:
            return "post"
        return "unknown"

    @staticmethod
    def extract_post_id(url):
        """
        Extract the post/reel shortcode from an Instagram URL.

        Args:
            url: str

        Returns:
            str | None - shortcode or None if not found
        """
        patterns = [
            r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
            r"instagr\.am/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    # ── Internal ─────────────────────────────────────────────────

    def _add_reference(self, queue_key, url, note, source_account):
        """Add a reference to a specific queue."""
        data = self._read()

        # Check for duplicate URL
        for ref in data[queue_key]:
            if ref["url"] == url and ref["status"] == "new":
                print("[ReferenceQueue] URL already in queue: %s" % url)
                return ref

        post_id = self.extract_post_id(url)
        now = datetime.now(timezone.utc)
        import uuid
        ref_id = "ref_%s_%s_%s" % (
            queue_key.replace("_references", ""),
            now.strftime("%Y%m%d_%H%M%S"),
            uuid.uuid4().hex[:6],
        )

        entry = {
            "ref_id": ref_id,
            "url": url,
            "post_id": post_id,
            "source_account": source_account,
            "note": note,
            "status": "new",
            "added_at": now.isoformat(),
            "used_in": None,
            "used_at": None,
        }

        data[queue_key].append(entry)
        self._write(data)

        print("[ReferenceQueue] Added %s to %s: %s" % (ref_id, queue_key, url))
        return entry

    def _get_next(self, queue_key):
        """Get the oldest 'new' reference from a queue."""
        data = self._read()

        for ref in data[queue_key]:
            if ref["status"] == "new":
                return ref

        return None

    def _list(self, queue_key, status):
        """List references from a queue, optionally filtered."""
        data = self._read()
        refs = data.get(queue_key, [])

        if status is not None:
            refs = [r for r in refs if r.get("status") == status]

        return refs

    def _ensure_file(self):
        """Ensure the queue file exists with proper structure."""
        if not os.path.exists(self.queue_file):
            os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
            empty = {
                "post_references": [],
                "reel_references": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(empty)
            print("[ReferenceQueue] Created queue file: %s" % self.queue_file)

    def _read(self):
        """Read the queue file with shared lock."""
        with open(self.queue_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return data

    def _write(self, data):
        """Write the queue file with exclusive lock."""
        tmp_path = self.queue_file + ".tmp"
        with open(tmp_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, self.queue_file)


# ── CLI for testing ──────────────────────────────────────────────

def main():
    import sys

    rq = ReferenceQueue()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 reference_queue.py add-post <url> [note]")
        print("  python3 reference_queue.py add-reel <url> [note]")
        print("  python3 reference_queue.py add-auto <url> [note]  # auto-detect type")
        print("  python3 reference_queue.py next-post")
        print("  python3 reference_queue.py next-reel")
        print("  python3 reference_queue.py mark-used <ref_id> <package_id>")
        print("  python3 reference_queue.py list [post|reel] [new|used|archived]")
        print("  python3 reference_queue.py count")
        print("  python3 reference_queue.py remove <ref_id>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "add-post":
        note = sys.argv[3] if len(sys.argv) > 3 else None
        ref = rq.add_post_reference(sys.argv[2], note=note)
        print(json.dumps(ref, indent=2))

    elif cmd == "add-reel":
        note = sys.argv[3] if len(sys.argv) > 3 else None
        ref = rq.add_reel_reference(sys.argv[2], note=note)
        print(json.dumps(ref, indent=2))

    elif cmd == "add-auto":
        url = sys.argv[2]
        note = sys.argv[3] if len(sys.argv) > 3 else None
        url_type = ReferenceQueue.detect_url_type(url)
        if url_type == "reel":
            ref = rq.add_reel_reference(url, note=note)
        elif url_type == "post":
            ref = rq.add_post_reference(url, note=note)
        else:
            print("Cannot detect URL type. Use add-post or add-reel explicitly.")
            sys.exit(1)
        print("Detected as: %s" % url_type)
        print(json.dumps(ref, indent=2))

    elif cmd == "next-post":
        ref = rq.get_next_post_reference()
        if ref:
            print(json.dumps(ref, indent=2))
        else:
            print("No new post references in queue")

    elif cmd == "next-reel":
        ref = rq.get_next_reel_reference()
        if ref:
            print(json.dumps(ref, indent=2))
        else:
            print("No new reel references in queue")

    elif cmd == "mark-used":
        rq.mark_used(sys.argv[2], sys.argv[3])

    elif cmd == "list":
        ref_type = sys.argv[2] if len(sys.argv) > 2 else None
        status = sys.argv[3] if len(sys.argv) > 3 else None

        if ref_type == "post" or ref_type is None:
            posts = rq.list_post_references(status)
            if posts:
                print("=== Post References ===")
                for r in posts:
                    print("  %s | %s | %s | %s" % (
                        r["ref_id"], r["status"], r["url"][:60],
                        r.get("note", "")))

        if ref_type == "reel" or ref_type is None:
            reels = rq.list_reel_references(status)
            if reels:
                print("=== Reel References ===")
                for r in reels:
                    print("  %s | %s | %s | %s" % (
                        r["ref_id"], r["status"], r["url"][:60],
                        r.get("note", "")))

        if ref_type is None:
            counts = rq.count()
            print("\nCounts: %s" % json.dumps(counts))

    elif cmd == "count":
        counts = rq.count()
        print(json.dumps(counts, indent=2))

    elif cmd == "remove":
        rq.remove(sys.argv[2])

    else:
        print("Unknown command: %s" % cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
