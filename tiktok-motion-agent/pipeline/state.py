import csv
import datetime as dt
import json
import os
from pathlib import Path

from .config import STATE_PATH, DATA_DIR, RUNS_CSV, ACTIVE_STATUSES


GENERATION_ACTIVE_STATUSES = set(ACTIVE_STATUSES) | {"STARTED", "NEEDS_REFERENCE_IMAGE"}
PRE_PROVIDER_ACTIVE_STATUSES = {"STARTED", "NEEDS_REFERENCE_IMAGE"}


def current_worker_id() -> str:
    return (os.environ.get("MOTION_WORKER_ID") or os.environ.get("WORKER_ID") or "default").strip() or "default"


def max_active_generations() -> int:
    try:
        return max(1, int(os.environ.get("MAX_ACTIVE_GENERATIONS", "1")))
    except Exception:
        return 1


def pre_provider_active_ttl_seconds() -> int:
    """How long prepare/ref-generation states can block a worker.

    Isolated cron agents can exit after prepare/ref review without reaching a
    terminal status. Provider states still block normally, but pre-provider
    states expire so a stopped agent does not make the worker uncontrollable.
    """
    try:
        return max(60, int(os.environ.get("PRE_PROVIDER_ACTIVE_TTL_SECONDS", "2700")))
    except Exception:
        return 2700


def job_age_seconds(job_id: str | None) -> float | None:
    if not job_id or len(job_id) < 14:
        return None
    try:
        created = dt.datetime.strptime(job_id[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None
    return (dt.datetime.now(dt.timezone.utc) - created).total_seconds()


def active_item(job_id: str | None, status: str | None, created_at: str = "", provider: str = "", worker_id: str = "") -> dict | None:
    status = status or ""
    if status not in GENERATION_ACTIVE_STATUSES:
        return None
    age = job_age_seconds(job_id)
    if status in PRE_PROVIDER_ACTIVE_STATUSES and age is not None and age > pre_provider_active_ttl_seconds():
        return None
    return {
        "job_id": job_id or "",
        "status": status,
        "created_at": created_at,
        "provider": provider,
        "worker_id": worker_id,
        "age_seconds": int(age) if age is not None else None,
    }


def state_load():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"recent_video_ids": [], "jobs": []}


def state_save(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def active_generations_from_state(state: dict, exclude_job_id: str | None = None) -> list[dict]:
    """Return active unfinished generations from state.

    The local state file is the source of truth for in-flight video-provider work.
    For parallel workers, waiting-for-reference jobs also count as active because
    an agent may be between prepare -> image_generate -> complete.
    """
    active = []
    seen_job_ids = set()
    for job_id, info in (state.get("prepared_jobs") or {}).items():
        if exclude_job_id and job_id == exclude_job_id:
            continue
        row = info.get("row") or {}
        status = row.get("status") or ""
        item = active_item(
            job_id,
            status,
            created_at=row.get("created_at", ""),
            provider=row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            worker_id=row.get("worker_id") or info.get("worker_id") or "",
        )
        if item:
            seen_job_ids.add(job_id)
            active.append(item)

    # Fallback for older state shapes / interrupted writes.
    for job in state.get("jobs") or []:
        job_id = job.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        if job_id and job_id in seen_job_ids:
            continue
        status = job.get("status")
        item = active_item(
            job_id,
            status,
            created_at=job.get("created_at", ""),
            provider=job.get("provider") or job.get("video_provider", ""),
            worker_id=job.get("worker_id", ""),
        )
        if item:
            active.append(item)
    return active


def active_generation_from_state(state: dict, exclude_job_id: str | None = None) -> dict | None:
    active = active_generations_from_state(state, exclude_job_id=exclude_job_id)
    return active[0] if active else None


def active_generations_from_csv(exclude_job_id: str | None = None) -> list[dict]:
    if not RUNS_CSV.exists():
        return []
    try:
        with RUNS_CSV.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return []
    active = []
    seen_job_ids = set()
    for row in reversed(rows):
        job_id = row.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        if job_id and job_id in seen_job_ids:
            continue
        if job_id:
            seen_job_ids.add(job_id)
        status = row.get("status") or ""
        item = active_item(
            job_id,
            status,
            created_at=row.get("created_at", ""),
            provider=row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            worker_id=row.get("worker_id", ""),
        )
        if item:
            active.append(item)
    return active


def active_generation_from_csv(exclude_job_id: str | None = None) -> dict | None:
    active = active_generations_from_csv(exclude_job_id=exclude_job_id)
    return active[0] if active else None


def find_active_generations(exclude_job_id: str | None = None) -> list[dict]:
    state_active = active_generations_from_state(state_load(), exclude_job_id=exclude_job_id)
    if state_active:
        return state_active
    return active_generations_from_csv(exclude_job_id=exclude_job_id)


def find_active_generation(exclude_job_id: str | None = None) -> dict | None:
    active = find_active_generations(exclude_job_id=exclude_job_id)
    return active[0] if active else None


def assert_generation_slot_available(exclude_job_id: str | None = None, worker_id: str | None = None):
    worker_id = worker_id or current_worker_id()
    active = find_active_generations(exclude_job_id=exclude_job_id)
    same_worker = [item for item in active if (item.get("worker_id") or "default") == worker_id]
    if same_worker:
        item = same_worker[0]
        raise RuntimeError(
            json.dumps(
                {
                    "ok": False,
                    "code": "WORKER_GENERATION_ALREADY_RUNNING",
                    "message": "This motion worker already has an unfinished generation.",
                    "worker_id": worker_id,
                    "active_job_id": item.get("job_id", ""),
                    "active_status": item.get("status", ""),
                    "active_provider": item.get("provider", ""),
                    "active_created_at": item.get("created_at", ""),
                },
                ensure_ascii=False,
            )
        )
    max_active = max_active_generations()
    if len(active) < max_active:
        return
    item = active[0]
    raise RuntimeError(
        json.dumps(
            {
                "ok": False,
                "code": "MAX_ACTIVE_GENERATIONS_REACHED",
                "message": "Maximum active video generations reached. Please wait until one finishes.",
                "max_active_generations": max_active,
                "active_count": len(active),
                "active_job_id": item.get("job_id", ""),
                "active_status": item.get("status", ""),
                "active_provider": item.get("provider", ""),
                "active_created_at": item.get("created_at", ""),
                "active_jobs": active[:10],
            },
            ensure_ascii=False,
        )
    )


def assert_no_active_generation(exclude_job_id: str | None = None):
    """Backward-compatible guard name, now worker/max-active aware."""
    assert_generation_slot_available(exclude_job_id=exclude_job_id)
