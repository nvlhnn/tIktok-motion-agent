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
