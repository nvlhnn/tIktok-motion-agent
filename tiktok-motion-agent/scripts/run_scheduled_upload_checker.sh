#!/usr/bin/env bash
set -euo pipefail
cd /root/.openclaw/workspace/tiktok-motion-agent
.venv/bin/python motion_pipeline.py check-scheduled-uploads --live
