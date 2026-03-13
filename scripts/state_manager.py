#!/usr/bin/env python3
"""
State Manager - CRUD operations for package state.json files.

Handles:
- Package folder creation with standard structure
- state.json create / read / update
- Asset status updates
- Version management (save new version, rollback to previous)
- File locking (only 1 active job per package)
- Recovery (restart-safe, restore from state.json)

Usage:
    from state_manager import StateManager
    sm = StateManager()
    pkg = sm.create_package("poolside luxury", source={...})
    sm.update_asset_status(pkg["package_id"], "post_slide_1", "generated")
    sm.save_asset_version(pkg["package_id"], "post_slide_1", "path/to/file.png", "abc123")
    sm.rollback_asset(pkg["package_id"], "post_slide_1")
"""

import json
import os
import fcntl
import time
import re
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
PACKAGES_DIR = os.path.join(WORKSPACE, "packages")

# Valid state transitions
PACKAGE_STATUSES = [
    "draft", "planning", "production", "review",
    "text_review", "approved", "scheduled", "published",
]

ASSET_STATUSES = [
    "planned", "prompt_ready", "generating", "generated",
    "pending_review", "approved", "rejected", "archived",
]

ASSET_TYPES = ["post_slide", "story_frame", "reel_start_frame"]

GENERATORS = ["z_image", "nano_banana", "kling"]

REEL_STAGES = [
    "start_frame_pending", "start_frame_review", "start_frame_approved",
    "motion_rendering", "motion_review", "approved",
]

# Phase 1 limits
DEFAULT_LIMITS = {
    "max_post_slides": 4,
    "max_stories": 4,
    "max_reels": 1,
    "max_total": 9,
}


class StateManager:
    """Manages package state.json files with locking and versioning."""

    def __init__(self, workspace=None):
        self.workspace = workspace or WORKSPACE
        self.packages_dir = os.path.join(self.workspace, "packages")
        os.makedirs(self.packages_dir, exist_ok=True)

    # ── Package CRUD ─────────────────────────────────────────────

    def create_package(self, theme, source, package_id=None):
        """
        Create a new package with folder structure and initial state.json.

        Args:
            theme: str - package theme (e.g. "poolside luxury")
            source: dict - source info with keys: mode, post_reference, reel_reference
            package_id: str | None - custom ID, auto-generated if None

        Returns:
            dict - the created state.json contents
        """
        if not package_id:
            slug = re.sub(r"[^a-z0-9]+", "_", theme.lower().strip())[:30]
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            package_id = "pkg_%s_%s" % (timestamp, slug)

        pkg_dir = os.path.join(self.packages_dir, package_id)
        if os.path.exists(pkg_dir):
            raise ValueError("Package already exists: %s" % package_id)

        # Create folder structure
        for subdir in [
            "references", "analysis", "plan", "prompts",
            "generated/posts", "generated/stories", "generated/reels",
            "text", "versions",
        ]:
            os.makedirs(os.path.join(pkg_dir, subdir), exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()

        state = {
            "package_id": package_id,
            "theme": theme,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "source": source,
            "assets": [],
            "text": {
                "post_caption": None,
                "post_hashtags": None,
                "story_overlays": None,
                "reel_caption": None,
                "status": "pending",
            },
            "review_context": {
                "last_preview_message_ids": [],
                "last_user_feedback": None,
                "pending_revision_targets": [],
                "approved_asset_ids": [],
                "revision_count": 0,
            },
            "limits": DEFAULT_LIMITS.copy(),
            "job_lock": None,
            "logs": [],
        }

        self._write_state(package_id, state)
        self._log(state, "package_created", {"theme": theme})
        self._write_state(package_id, state)

        print("[StateManager] Created package: %s (%s)" % (package_id, theme))
        return state

    def get_package(self, package_id):
        """Read and return state.json for a package."""
        return self._read_state(package_id)

    def list_packages(self, status_filter=None):
        """List all packages, optionally filtered by status."""
        packages = []
        if not os.path.exists(self.packages_dir):
            return packages

        for name in sorted(os.listdir(self.packages_dir)):
            state_path = os.path.join(self.packages_dir, name, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path) as f:
                        state = json.load(f)
                    if status_filter is None or state.get("status") == status_filter:
                        packages.append(state)
                except (json.JSONDecodeError, IOError):
                    continue

        return packages

    def update_package_status(self, package_id, new_status):
        """Update the package-level status."""
        if new_status not in PACKAGE_STATUSES:
            raise ValueError("Invalid package status: %s. Valid: %s" % (
                new_status, ", ".join(PACKAGE_STATUSES)))

        state = self._read_state(package_id)
        old_status = state["status"]
        state["status"] = new_status
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._log(state, "status_changed", {
            "from": old_status, "to": new_status,
        })
        self._write_state(package_id, state)

        print("[StateManager] %s: status %s -> %s" % (package_id, old_status, new_status))
        return state

    # ── Asset Management ─────────────────────────────────────────

    def add_asset(self, package_id, asset_id, asset_type, order,
                  role, has_character, generator, **kwargs):
        """
        Add a new asset to the package.

        Args:
            package_id: str
            asset_id: str (e.g. "post_slide_1")
            asset_type: str - one of ASSET_TYPES
            order: int
            role: str (e.g. "hero_character", "world_detail")
            has_character: bool
            generator: str - one of GENERATORS
            **kwargs: extra fields (motion_ref, etc.)

        Returns:
            dict - the asset object
        """
        if asset_type not in ASSET_TYPES:
            raise ValueError("Invalid asset type: %s" % asset_type)
        if generator not in GENERATORS:
            raise ValueError("Invalid generator: %s" % generator)

        state = self._read_state(package_id)

        # Check limits
        type_counts = {}
        for a in state["assets"]:
            t = a["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        limits = state.get("limits", DEFAULT_LIMITS)
        if asset_type == "post_slide" and type_counts.get("post_slide", 0) >= limits["max_post_slides"]:
            raise ValueError("Max post slides (%d) reached" % limits["max_post_slides"])
        if asset_type == "story_frame" and type_counts.get("story_frame", 0) >= limits["max_stories"]:
            raise ValueError("Max stories (%d) reached" % limits["max_stories"])
        if asset_type == "reel_start_frame" and type_counts.get("reel_start_frame", 0) >= limits["max_reels"]:
            raise ValueError("Max reels (%d) reached" % limits["max_reels"])
        if len(state["assets"]) >= limits["max_total"]:
            raise ValueError("Max total assets (%d) reached" % limits["max_total"])

        # Check duplicate
        for a in state["assets"]:
            if a["asset_id"] == asset_id:
                raise ValueError("Asset already exists: %s" % asset_id)

        asset = {
            "asset_id": asset_id,
            "type": asset_type,
            "role": role,
            "order": order,
            "has_character": has_character,
            "generator": generator,
            "active_version": 0,
            "versions": [],
            "status": "planned",
        }

        # Reel-specific fields
        if asset_type == "reel_start_frame":
            asset["start_frame"] = None
            asset["motion_ref"] = kwargs.get("motion_ref")
            asset["reel_stage"] = "start_frame_pending"
            asset["reel_output"] = None

        state["assets"].append(asset)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._log(state, "asset_added", {
            "asset_id": asset_id, "type": asset_type,
            "generator": generator, "has_character": has_character,
        })
        self._write_state(package_id, state)

        print("[StateManager] %s: added asset %s (%s, %s)" % (
            package_id, asset_id, asset_type, generator))
        return asset

    def update_asset_status(self, package_id, asset_id, new_status):
        """Update the status of a specific asset."""
        if new_status not in ASSET_STATUSES:
            raise ValueError("Invalid asset status: %s" % new_status)

        state = self._read_state(package_id)
        asset = self._find_asset(state, asset_id)

        old_status = asset["status"]
        asset["status"] = new_status
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update review_context
        review = state["review_context"]
        if new_status == "approved" and asset_id not in review["approved_asset_ids"]:
            review["approved_asset_ids"].append(asset_id)
            if asset_id in review["pending_revision_targets"]:
                review["pending_revision_targets"].remove(asset_id)
        elif new_status == "rejected":
            if asset_id not in review["pending_revision_targets"]:
                review["pending_revision_targets"].append(asset_id)
            if asset_id in review["approved_asset_ids"]:
                review["approved_asset_ids"].remove(asset_id)

        self._log(state, "asset_status_changed", {
            "asset_id": asset_id, "from": old_status, "to": new_status,
        })
        self._write_state(package_id, state)

        print("[StateManager] %s/%s: status %s -> %s" % (
            package_id, asset_id, old_status, new_status))
        return asset

    def update_reel_stage(self, package_id, asset_id, new_stage):
        """Update the reel-specific stage for a reel asset."""
        if new_stage not in REEL_STAGES:
            raise ValueError("Invalid reel stage: %s" % new_stage)

        state = self._read_state(package_id)
        asset = self._find_asset(state, asset_id)

        if asset["type"] != "reel_start_frame":
            raise ValueError("Asset %s is not a reel" % asset_id)

        old_stage = asset.get("reel_stage")
        asset["reel_stage"] = new_stage
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._log(state, "reel_stage_changed", {
            "asset_id": asset_id, "from": old_stage, "to": new_stage,
        })
        self._write_state(package_id, state)
        return asset

    # ── Version Management ───────────────────────────────────────

    def save_asset_version(self, package_id, asset_id, file_path, prompt_hash,
                           generation_time_sec=0):
        """
        Save a new version for an asset. The new version becomes active.

        Args:
            package_id: str
            asset_id: str
            file_path: str - path to generated file (relative to package dir)
            prompt_hash: str - hash of the prompt used
            generation_time_sec: float

        Returns:
            dict - the new version entry
        """
        state = self._read_state(package_id)
        asset = self._find_asset(state, asset_id)

        new_version_num = len(asset["versions"]) + 1
        now = datetime.now(timezone.utc).isoformat()

        version_entry = {
            "version": new_version_num,
            "file": file_path,
            "prompt_hash": prompt_hash,
            "generated_at": now,
            "generation_time_sec": generation_time_sec,
        }

        asset["versions"].append(version_entry)
        asset["active_version"] = new_version_num
        asset["status"] = "generated"

        # For reels, update start_frame path
        if asset["type"] == "reel_start_frame" and asset.get("reel_stage") in (
            "start_frame_pending", None
        ):
            asset["start_frame"] = file_path
            asset["reel_stage"] = "start_frame_review"

        state["updated_at"] = now

        self._log(state, "version_saved", {
            "asset_id": asset_id, "version": new_version_num,
            "file": file_path,
        })
        self._write_state(package_id, state)

        print("[StateManager] %s/%s: saved version %d (%s)" % (
            package_id, asset_id, new_version_num, file_path))
        return version_entry

    def rollback_asset(self, package_id, asset_id):
        """
        Rollback an asset to its previous active version.
        The current version stays in history but is no longer active.

        Returns:
            dict - the now-active version entry, or None if no previous version
        """
        state = self._read_state(package_id)
        asset = self._find_asset(state, asset_id)

        current_version = asset["active_version"]
        if current_version <= 1:
            print("[StateManager] %s/%s: cannot rollback, only 1 version" % (
                package_id, asset_id))
            return None

        # Find the previous approved version, or just the previous one
        previous_version = current_version - 1
        asset["active_version"] = previous_version
        asset["status"] = "approved"  # restored version was previously approved

        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._log(state, "version_rollback", {
            "asset_id": asset_id,
            "from_version": current_version,
            "to_version": previous_version,
        })
        self._write_state(package_id, state)

        version_entry = asset["versions"][previous_version - 1]
        print("[StateManager] %s/%s: rolled back v%d -> v%d (%s)" % (
            package_id, asset_id, current_version, previous_version,
            version_entry["file"]))
        return version_entry

    def get_active_file(self, package_id, asset_id):
        """Get the file path of the currently active version of an asset."""
        state = self._read_state(package_id)
        asset = self._find_asset(state, asset_id)

        active_ver = asset["active_version"]
        if active_ver <= 0 or not asset["versions"]:
            return None

        return asset["versions"][active_ver - 1]["file"]

    # ── Text Management ──────────────────────────────────────────

    def update_text(self, package_id, post_caption=None, post_hashtags=None,
                    story_overlays=None, reel_caption=None, text_status=None):
        """Update text fields in the package."""
        state = self._read_state(package_id)

        if post_caption is not None:
            state["text"]["post_caption"] = post_caption
        if post_hashtags is not None:
            state["text"]["post_hashtags"] = post_hashtags
        if story_overlays is not None:
            state["text"]["story_overlays"] = story_overlays
        if reel_caption is not None:
            state["text"]["reel_caption"] = reel_caption
        if text_status is not None:
            state["text"]["status"] = text_status

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._log(state, "text_updated", {"status": state["text"]["status"]})
        self._write_state(package_id, state)
        return state["text"]

    # ── Review Context ───────────────────────────────────────────

    def update_review_context(self, package_id, **kwargs):
        """Update review context fields."""
        state = self._read_state(package_id)

        for key, value in kwargs.items():
            if key in state["review_context"]:
                state["review_context"][key] = value

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_state(package_id, state)
        return state["review_context"]

    def increment_revision_count(self, package_id):
        """Increment the revision counter."""
        state = self._read_state(package_id)
        state["review_context"]["revision_count"] += 1
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_state(package_id, state)
        return state["review_context"]["revision_count"]

    # ── Job Locking ──────────────────────────────────────────────

    def acquire_lock(self, package_id, job_name, timeout=600):
        """
        Acquire a job lock on a package. Only one active job at a time.

        Args:
            package_id: str
            job_name: str - description of the job
            timeout: int - max seconds before lock auto-expires

        Returns:
            str - lock_id if acquired

        Raises:
            RuntimeError if lock is held by another job
        """
        state = self._read_state(package_id)
        now = datetime.now(timezone.utc)

        # Check existing lock
        lock = state.get("job_lock")
        if lock is not None:
            lock_time = datetime.fromisoformat(lock["acquired_at"])
            elapsed = (now - lock_time).total_seconds()
            if elapsed < lock.get("timeout", timeout):
                raise RuntimeError(
                    "Package %s is locked by job '%s' (acquired %ds ago). "
                    "Cannot start '%s'." % (
                        package_id, lock["job_name"],
                        int(elapsed), job_name))
            else:
                print("[StateManager] %s: expired lock from '%s' (%.0fs ago), overriding" % (
                    package_id, lock["job_name"], elapsed))

        lock_id = "%s_%s" % (job_name, now.strftime("%Y%m%d_%H%M%S"))
        state["job_lock"] = {
            "lock_id": lock_id,
            "job_name": job_name,
            "acquired_at": now.isoformat(),
            "timeout": timeout,
        }
        state["updated_at"] = now.isoformat()

        self._log(state, "lock_acquired", {"job_name": job_name, "lock_id": lock_id})
        self._write_state(package_id, state)

        print("[StateManager] %s: lock acquired for '%s'" % (package_id, job_name))
        return lock_id

    def release_lock(self, package_id, lock_id=None):
        """
        Release the job lock on a package.

        Args:
            package_id: str
            lock_id: str | None - if provided, only release if it matches
        """
        state = self._read_state(package_id)
        lock = state.get("job_lock")

        if lock is None:
            return

        if lock_id and lock.get("lock_id") != lock_id:
            print("[StateManager] %s: lock_id mismatch, not releasing" % package_id)
            return

        job_name = lock.get("job_name", "unknown")
        state["job_lock"] = None
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._log(state, "lock_released", {"job_name": job_name})
        self._write_state(package_id, state)

        print("[StateManager] %s: lock released (was '%s')" % (package_id, job_name))

    def is_locked(self, package_id):
        """Check if a package has an active (non-expired) lock."""
        state = self._read_state(package_id)
        lock = state.get("job_lock")

        if lock is None:
            return False

        lock_time = datetime.fromisoformat(lock["acquired_at"])
        elapsed = (datetime.now(timezone.utc) - lock_time).total_seconds()
        return elapsed < lock.get("timeout", 600)

    # ── Recovery ─────────────────────────────────────────────────

    def get_recovery_state(self, package_id):
        """
        Get recovery info for a package after restart.
        Returns the state + what step to resume from.
        """
        state = self._read_state(package_id)

        # Clear expired locks
        lock = state.get("job_lock")
        if lock:
            lock_time = datetime.fromisoformat(lock["acquired_at"])
            elapsed = (datetime.now(timezone.utc) - lock_time).total_seconds()
            if elapsed >= lock.get("timeout", 600):
                state["job_lock"] = None
                self._write_state(package_id, state)
                print("[StateManager] %s: cleared expired lock during recovery" % package_id)

        # Determine resume point
        resume_info = {
            "package_status": state["status"],
            "assets_planned": 0,
            "assets_generated": 0,
            "assets_approved": 0,
            "assets_pending": [],
            "generating_assets": [],
            "text_status": state["text"]["status"],
            "has_lock": state.get("job_lock") is not None,
        }

        for asset in state["assets"]:
            resume_info["assets_planned"] += 1
            status = asset["status"]
            if status in ("generated", "pending_review"):
                resume_info["assets_generated"] += 1
            elif status == "approved":
                resume_info["assets_approved"] += 1
            elif status == "generating":
                resume_info["generating_assets"].append(asset["asset_id"])
            elif status in ("planned", "prompt_ready"):
                resume_info["assets_pending"].append(asset["asset_id"])

        return state, resume_info

    # ── Package path helpers ─────────────────────────────────────

    def get_package_dir(self, package_id):
        """Get the absolute path to a package directory."""
        return os.path.join(self.packages_dir, package_id)

    def get_package_subdir(self, package_id, subdir):
        """Get path to a subdirectory within a package."""
        path = os.path.join(self.packages_dir, package_id, subdir)
        os.makedirs(path, exist_ok=True)
        return path

    # ── Helpers ─────────────────────────────────────────────

    def is_package_fully_approved(self, package_id):
        """Check if all assets + text are approved."""
        state = self._read_state(package_id)

        for asset in state["assets"]:
            if asset["status"] != "approved":
                return False

        if state["text"]["status"] != "approved":
            return False

        return True

    def get_asset_summary(self, package_id):
        """Get a summary of asset counts and statuses."""
        state = self._read_state(package_id)
        summary = {
            "total": len(state["assets"]),
            "by_type": {},
            "by_status": {},
            "character_count": 0,
            "world_count": 0,
        }

        for asset in state["assets"]:
            t = asset["type"]
            s = asset["status"]
            summary["by_type"][t] = summary["by_type"].get(t, 0) + 1
            summary["by_status"][s] = summary["by_status"].get(s, 0) + 1
            if asset["has_character"]:
                summary["character_count"] += 1
            else:
                summary["world_count"] += 1

        total = summary["total"]
        if total > 0:
            summary["character_ratio"] = round(summary["character_count"] / total, 2)
        else:
            summary["character_ratio"] = 0

        return summary

    # ── Internal I/O with file locking ───────────────────────────

    def _read_state(self, package_id):
        """Read state.json with file-level locking."""
        state_path = os.path.join(self.packages_dir, package_id, "state.json")

        if not os.path.exists(state_path):
            raise FileNotFoundError("Package not found: %s" % package_id)

        with open(state_path, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                state = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return state

    def _write_state(self, package_id, state):
        """Write state.json with exclusive file-level locking."""
        pkg_dir = os.path.join(self.packages_dir, package_id)
        state_path = os.path.join(pkg_dir, "state.json")
        tmp_path = state_path + ".tmp"

        os.makedirs(pkg_dir, exist_ok=True)

        with open(tmp_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        os.replace(tmp_path, state_path)

    def _find_asset(self, state, asset_id):
        """Find an asset by ID within a state dict. Raises if not found."""
        for asset in state["assets"]:
            if asset["asset_id"] == asset_id:
                return asset
        raise ValueError("Asset not found: %s in package %s" % (
            asset_id, state["package_id"]))

    def _log(self, state, event, details=None):
        """Append a log entry to state."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        if details:
            entry["details"] = details

        # Keep last 100 log entries
        state["logs"].append(entry)
        if len(state["logs"]) > 100:
            state["logs"] = state["logs"][-100:]


# ── CLI for testing ──────────────────────────────────────────────

def main():
    import sys

    sm = StateManager()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 state_manager.py create <theme>")
        print("  python3 state_manager.py list [status]")
        print("  python3 state_manager.py get <package_id>")
        print("  python3 state_manager.py add-asset <package_id> <asset_id> <type> <order> <role> <has_char> <generator>")
        print("  python3 state_manager.py update-status <package_id> <asset_id> <new_status>")
        print("  python3 state_manager.py save-version <package_id> <asset_id> <file> <hash>")
        print("  python3 state_manager.py rollback <package_id> <asset_id>")
        print("  python3 state_manager.py lock <package_id> <job_name>")
        print("  python3 state_manager.py unlock <package_id>")
        print("  python3 state_manager.py summary <package_id>")
        print("  python3 state_manager.py recovery <package_id>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "create":
        theme = sys.argv[2] if len(sys.argv) > 2 else "test theme"
        source = {"mode": "manual_queue", "post_reference": None, "reel_reference": None}
        pkg = sm.create_package(theme, source)
        print(json.dumps(pkg, indent=2, ensure_ascii=False))

    elif cmd == "list":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        packages = sm.list_packages(status)
        for p in packages:
            print("%s | %s | %s | assets: %d" % (
                p["package_id"], p["status"], p["theme"], len(p["assets"])))

    elif cmd == "get":
        pkg = sm.get_package(sys.argv[2])
        print(json.dumps(pkg, indent=2, ensure_ascii=False))

    elif cmd == "add-asset":
        # add-asset <pkg_id> <asset_id> <type> <order> <role> <has_char> <generator>
        asset = sm.add_asset(
            sys.argv[2], sys.argv[3], sys.argv[4],
            int(sys.argv[5]), sys.argv[6],
            sys.argv[7].lower() == "true", sys.argv[8],
        )
        print(json.dumps(asset, indent=2))

    elif cmd == "update-status":
        sm.update_asset_status(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "save-version":
        ver = sm.save_asset_version(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        print(json.dumps(ver, indent=2))

    elif cmd == "rollback":
        ver = sm.rollback_asset(sys.argv[2], sys.argv[3])
        if ver:
            print(json.dumps(ver, indent=2))
        else:
            print("No previous version to rollback to")

    elif cmd == "lock":
        lock_id = sm.acquire_lock(sys.argv[2], sys.argv[3])
        print("Lock acquired: %s" % lock_id)

    elif cmd == "unlock":
        sm.release_lock(sys.argv[2])

    elif cmd == "summary":
        summary = sm.get_asset_summary(sys.argv[2])
        print(json.dumps(summary, indent=2))

    elif cmd == "recovery":
        state, info = sm.get_recovery_state(sys.argv[2])
        print("Recovery info:")
        print(json.dumps(info, indent=2))

    else:
        print("Unknown command: %s" % cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
