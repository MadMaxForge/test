"""
State Manager — job CRUD, asset versioning, job locking, recovery.

Each content job is stored as a JSON file in JOBS_DIR/<job_id>/state.json.
File-based locking via fcntl ensures safe concurrent access.

This is a pure Python module — no LLM calls.
"""

import json
import fcntl
import uuid
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from content_module.core.config import JOBS_DIR, JOB_STATES, ASSET_STATES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _lock_path(job_id: str) -> Path:
    return _job_dir(job_id) / ".lock"


class JobLock:
    """File-based lock for a job. Use as context manager."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.lock_file = None

    def __enter__(self):
        lock_path = _lock_path(self.job_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = open(lock_path, "w")
        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()
        return False


def _read_state(job_id: str) -> dict:
    path = _state_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"Job not found: {job_id}")
    with open(path) as f:
        return json.load(f)


def _write_state(job_id: str, state: dict) -> None:
    state["updated_at"] = _now_iso()
    path = _state_path(job_id)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.rename(path)


# ── Job CRUD ─────────────────────────────────────────────────────

def create_job(
    content_type: str,
    reference_url: str,
    reference_type: str = "post",
) -> str:
    """
    Create a new content job.

    Args:
        content_type: 'post', 'story', or 'reel'
        reference_url: Instagram URL
        reference_type: 'post' or 'reel'

    Returns:
        job_id
    """
    if content_type not in ("post", "story", "reel"):
        raise ValueError(f"Invalid content_type: {content_type}")

    job_id = f"{content_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    for subdir in ("references", "analysis", "plan", "prompts", "generated", "text", "versions"):
        (job_dir / subdir).mkdir(exist_ok=True)

    state = {
        "job_id": job_id,
        "content_type": content_type,
        "status": "draft",
        "reference_url": reference_url,
        "reference_type": reference_type,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "assets": [],
        "analysis": None,
        "plan": None,
        "prompts": None,
        "text": None,
        "active_version": 1,
        "error": None,
    }

    _write_state(job_id, state)
    return job_id


def get_job(job_id: str) -> dict:
    """Read job state."""
    return _read_state(job_id)


def update_job_status(job_id: str, status: str, error: Optional[str] = None) -> dict:
    """Update job status."""
    if status not in JOB_STATES:
        raise ValueError(f"Invalid status: {status}. Must be one of {JOB_STATES}")
    with JobLock(job_id):
        state = _read_state(job_id)
        state["status"] = status
        if error is not None:
            state["error"] = error
        _write_state(job_id, state)
        return state


def set_analysis(job_id: str, analysis: dict) -> dict:
    """Store analysis result."""
    with JobLock(job_id):
        state = _read_state(job_id)
        state["analysis"] = analysis
        # Also save to file
        analysis_path = _job_dir(job_id) / "analysis" / "analysis.json"
        with open(analysis_path, "w") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
        _write_state(job_id, state)
        return state


def set_plan(job_id: str, plan: dict) -> dict:
    """Store plan and initialize assets from it."""
    with JobLock(job_id):
        state = _read_state(job_id)
        state["plan"] = plan

        # Initialize assets from plan
        assets = []
        for i, asset_plan in enumerate(plan.get("assets", [])):
            assets.append({
                "index": i,
                "role": asset_plan.get("role", f"slide_{i+1}"),
                "has_character": asset_plan.get("has_character", False),
                "generator": asset_plan.get("generator", "unknown"),
                "status": "pending",
                "prompt": None,
                "file_path": None,
                "version": 1,
                "versions": [],
            })
        state["assets"] = assets

        # Save plan to file
        plan_path = _job_dir(job_id) / "plan" / "plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        _write_state(job_id, state)
        return state


def set_prompts(job_id: str, prompts: list[dict]) -> dict:
    """Store prompts for each asset."""
    with JobLock(job_id):
        state = _read_state(job_id)
        state["prompts"] = prompts

        for prompt_data in prompts:
            idx = prompt_data.get("index", -1)
            if 0 <= idx < len(state["assets"]):
                state["assets"][idx]["prompt"] = prompt_data.get("prompt", "")

        # Save prompts to file
        prompts_path = _job_dir(job_id) / "prompts" / "prompts.json"
        with open(prompts_path, "w") as f:
            json.dump(prompts, f, indent=2, ensure_ascii=False)

        _write_state(job_id, state)
        return state


def update_asset(
    job_id: str,
    asset_index: int,
    status: Optional[str] = None,
    file_path: Optional[str] = None,
) -> dict:
    """Update a single asset's status or file path."""
    with JobLock(job_id):
        state = _read_state(job_id)
        if asset_index < 0 or asset_index >= len(state["assets"]):
            raise IndexError(f"Asset index {asset_index} out of range")

        asset = state["assets"][asset_index]
        if status is not None:
            if status not in ASSET_STATES:
                raise ValueError(f"Invalid asset status: {status}")
            asset["status"] = status
        if file_path is not None:
            # Save version history
            if asset["file_path"]:
                asset["versions"].append({
                    "version": asset["version"],
                    "file_path": asset["file_path"],
                    "timestamp": _now_iso(),
                })
            asset["file_path"] = file_path
            asset["version"] += 1

        _write_state(job_id, state)
        return state


def approve_asset(job_id: str, asset_index: int) -> dict:
    """Mark asset as approved."""
    return update_asset(job_id, asset_index, status="approved")


def reject_asset(job_id: str, asset_index: int) -> dict:
    """Mark asset as rejected (needs regeneration)."""
    return update_asset(job_id, asset_index, status="rejected")


def set_text(job_id: str, text_data: dict) -> dict:
    """Store caption/hashtags/text for the job."""
    with JobLock(job_id):
        state = _read_state(job_id)
        state["text"] = text_data

        text_path = _job_dir(job_id) / "text" / "text.json"
        with open(text_path, "w") as f:
            json.dump(text_data, f, indent=2, ensure_ascii=False)

        _write_state(job_id, state)
        return state


def save_version_snapshot(job_id: str) -> str:
    """Save current state as a named version snapshot."""
    with JobLock(job_id):
        state = _read_state(job_id)
        version = state["active_version"]
        version_dir = _job_dir(job_id) / "versions" / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # Copy state
        with open(version_dir / "state.json", "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

        # Copy generated files
        gen_dir = _job_dir(job_id) / "generated"
        if gen_dir.exists():
            version_gen = version_dir / "generated"
            if version_gen.exists():
                shutil.rmtree(version_gen)
            shutil.copytree(gen_dir, version_gen)

        state["active_version"] = version + 1
        _write_state(job_id, state)
        return str(version_dir)


# ── Listing & Recovery ───────────────────────────────────────────

def list_jobs(status_filter: Optional[str] = None) -> list[dict]:
    """List all jobs, optionally filtered by status."""
    jobs = []
    if not JOBS_DIR.exists():
        return jobs
    for job_dir in sorted(JOBS_DIR.iterdir()):
        state_file = job_dir / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                if status_filter is None or state.get("status") == status_filter:
                    jobs.append(state)
            except json.JSONDecodeError:
                continue
    return jobs


def get_active_jobs() -> list[dict]:
    """Get jobs that are in-progress (not approved/failed)."""
    terminal = {"approved", "failed"}
    return [j for j in list_jobs() if j.get("status") not in terminal]


def get_recovery_state() -> list[dict]:
    """Find jobs that were interrupted and can be resumed."""
    resumable = {"analyzing", "planning", "prompting", "generating", "review", "revising", "text_review"}
    return [j for j in list_jobs() if j.get("status") in resumable]


def delete_job(job_id: str) -> bool:
    """Delete a job and all its files."""
    job_dir = _job_dir(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)
        return True
    return False
