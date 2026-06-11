"""Small file-lock helpers for shared pipeline state.

Generation crons can run in separate agent processes. This lock serializes the
critical sections that read/write state.json, runs.csv and source reservations.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DATA_DIR


LOCK_PATH = DATA_DIR / "state.lock"


@contextmanager
def state_lock(timeout_seconds: float | None = None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timeout_seconds = timeout_seconds if timeout_seconds is not None else float(os.environ.get("STATE_LOCK_TIMEOUT_SECONDS", "300"))
    deadline = time.monotonic() + max(0, timeout_seconds)
    with LOCK_PATH.open("a+") as f:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for state lock: {LOCK_PATH}")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
