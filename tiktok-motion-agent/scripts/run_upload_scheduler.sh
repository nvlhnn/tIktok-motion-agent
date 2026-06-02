#!/usr/bin/env bash
set -euo pipefail
cd /root/.openclaw/workspace/tiktok-motion-agent
.venv/bin/python - <<'PY'
import json
import motion_pipeline as m
m.load_env()
if not m.in_upload_slot():
    print(json.dumps({"skipped": "outside_upload_window", "windows": m.upload_windows()}, ensure_ascii=False))
else:
    print(json.dumps(m.upload_scheduler(dry_run=False, live=True), ensure_ascii=False))
PY
