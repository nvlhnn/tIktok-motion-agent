import csv
import datetime as dt
import json

import pytest

from pipeline import state as state_mod


def test_generation_slot_allows_second_worker(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("MAX_ACTIVE_GENERATIONS", "2")
    monkeypatch.setenv("MOTION_WORKER_ID", "B")
    state_path.write_text(json.dumps({
        "prepared_jobs": {
            "job-a": {"row": {"status": "NEEDS_REFERENCE_IMAGE", "worker_id": "A"}}
        },
        "jobs": [],
    }))

    state_mod.assert_generation_slot_available()


def test_generation_slot_blocks_same_worker(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("MAX_ACTIVE_GENERATIONS", "2")
    monkeypatch.setenv("MOTION_WORKER_ID", "A")
    state_path.write_text(json.dumps({
        "prepared_jobs": {
            "job-a": {"row": {"status": "NEEDS_REFERENCE_IMAGE", "worker_id": "A"}}
        },
        "jobs": [],
    }))

    with pytest.raises(RuntimeError) as err:
        state_mod.assert_generation_slot_available()
    assert "WORKER_GENERATION_ALREADY_RUNNING" in str(err.value)


def test_generation_slot_blocks_when_max_active_reached(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("MAX_ACTIVE_GENERATIONS", "2")
    monkeypatch.setenv("MOTION_WORKER_ID", "C")
    state_path.write_text(json.dumps({
        "prepared_jobs": {
            "job-a": {"row": {"status": "NEEDS_REFERENCE_IMAGE", "worker_id": "A"}},
            "job-b": {"row": {"status": "PROCESSING", "worker_id": "B"}},
        },
        "jobs": [],
    }))

    with pytest.raises(RuntimeError) as err:
        state_mod.assert_generation_slot_available()
    assert "MAX_ACTIVE_GENERATIONS_REACHED" in str(err.value)


def _job_id(age_seconds: int) -> str:
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_seconds)
    return created.strftime("%Y%m%d%H%M%S") + "-test00"


def test_stale_pre_provider_job_does_not_block_worker(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    stale_job = _job_id(3600)
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("MAX_ACTIVE_GENERATIONS", "2")
    monkeypatch.setenv("PRE_PROVIDER_ACTIVE_TTL_SECONDS", "600")
    monkeypatch.setenv("MOTION_WORKER_ID", "A")
    state_path.write_text(json.dumps({
        "prepared_jobs": {
            stale_job: {"row": {"status": "NEEDS_REFERENCE_IMAGE", "worker_id": "A"}}
        },
        "jobs": [],
    }))

    state_mod.assert_generation_slot_available()


def test_provider_processing_job_still_blocks_after_ttl(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    old_job = _job_id(3600)
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("PRE_PROVIDER_ACTIVE_TTL_SECONDS", "600")
    monkeypatch.setenv("MOTION_WORKER_ID", "A")
    state_path.write_text(json.dumps({
        "prepared_jobs": {
            old_job: {"row": {"status": "PROCESSING", "worker_id": "A"}}
        },
        "jobs": [],
    }))

    with pytest.raises(RuntimeError) as err:
        state_mod.assert_generation_slot_available()
    assert "WORKER_GENERATION_ALREADY_RUNNING" in str(err.value)


def test_csv_uses_latest_row_per_job(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    runs_path = tmp_path / "runs.csv"
    job = _job_id(60)
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "RUNS_CSV", runs_path)
    monkeypatch.setenv("MOTION_WORKER_ID", "A")
    state_path.write_text(json.dumps({"prepared_jobs": {}, "jobs": []}))
    with runs_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["job_id", "status", "worker_id", "created_at"])
        writer.writeheader()
        writer.writerow({"job_id": job, "status": "NEEDS_REFERENCE_IMAGE", "worker_id": "A", "created_at": "old"})
        writer.writerow({"job_id": job, "status": "REJECTED", "worker_id": "A", "created_at": "new"})

    state_mod.assert_generation_slot_available()
