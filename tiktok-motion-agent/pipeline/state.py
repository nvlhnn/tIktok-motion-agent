import csv
import json
from pathlib import Path

from .config import STATE_PATH, DATA_DIR, RUNS_CSV, ACTIVE_STATUSES


def state_load():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"recent_video_ids": [], "jobs": []}


def state_save(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def active_generation_from_state(state: dict, exclude_job_id: str | None = None) -> dict | None:
    """Return an active unfinished generation, if any.

    The local state file is the source of truth for in-flight video-provider work.
    Waiting-for-reference jobs are intentionally not considered running; only
    queued/submitted/processing provider jobs block a new generation.
    """
    for job_id, info in (state.get("prepared_jobs") or {}).items():
        if exclude_job_id and job_id == exclude_job_id:
            continue
        row = info.get("row") or {}
        status = row.get("status") or ""
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id,
                "status": status,
                "created_at": row.get("created_at", ""),
                "provider": row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            }

    # Fallback for older state shapes / interrupted writes.
    for job in state.get("jobs") or []:
        job_id = job.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        status = job.get("status")
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id or "",
                "status": status,
                "created_at": job.get("created_at", ""),
                "provider": job.get("provider") or job.get("video_provider", ""),
            }
    return None


def active_generation_from_csv(exclude_job_id: str | None = None) -> dict | None:
    if not RUNS_CSV.exists():
        return None
    try:
        with RUNS_CSV.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return None
    for row in reversed(rows):
        job_id = row.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        status = row.get("status") or ""
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id or "",
                "status": status,
                "created_at": row.get("created_at", ""),
                "provider": row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            }
    return None


def find_active_generation(exclude_job_id: str | None = None) -> dict | None:
    state_active = active_generation_from_state(state_load(), exclude_job_id=exclude_job_id)
    if state_active:
        return state_active
    return active_generation_from_csv(exclude_job_id=exclude_job_id)


def assert_no_active_generation(exclude_job_id: str | None = None):
    active = find_active_generation(exclude_job_id=exclude_job_id)
    if not active:
        return
    raise RuntimeError(
        json.dumps(
            {
                "ok": False,
                "code": "GENERATION_ALREADY_RUNNING",
                "message": "A video generation is already running. Please wait until it finishes.",
                "active_job_id": active.get("job_id", ""),
                "active_status": active.get("status", ""),
                "active_provider": active.get("provider", ""),
                "active_created_at": active.get("created_at", ""),
            },
            ensure_ascii=False,
        )
    )
