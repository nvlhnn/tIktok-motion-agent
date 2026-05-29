import datetime as dt
import json
import os
import random
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests

from .config import require_env, int_env, truthy_env
from .utils import now_utc, indonesia_pretty_datetime, parse_hhmm
from .state import state_load, state_save
from .storage import log_row, load_run_rows


def upload_slots() -> list[str]:
    raw = os.environ.get("TIKTOK_UPLOAD_SLOTS", "08:00,12:30,16:30,20:30")
    return [s.strip() for s in re.split(r"[,\n]", raw) if s.strip()]


def upload_windows() -> list[str]:
    """Optional randomized upload windows, e.g. 07:45-09:15,11:30-13:00."""
    raw = os.environ.get("TIKTOK_UPLOAD_WINDOWS", "")
    return [s.strip() for s in re.split(r"[,\n]", raw) if s.strip()]


def randomized_upload_window(now: dt.datetime | None = None) -> dict | None:
    """Return today's active randomized window, if any.

    The trigger minute is deterministic for a given local date + window + salt so
    repeated cron checks during the day agree on the same random-looking time.
    """
    windows = upload_windows()
    if not windows:
        return None
    tz = ZoneInfo(os.environ.get("TIKTOK_UPLOAD_TIMEZONE", "Asia/Jakarta"))
    now = (now or now_utc()).astimezone(tz)
    trigger_window = int_env("TIKTOK_UPLOAD_TRIGGER_WINDOW_MINUTES", 10)
    salt = os.environ.get("TIKTOK_UPLOAD_RANDOM_SALT") or buffer_channel_id(test=False)
    today = now.strftime("%Y-%m-%d")
    for window in windows:
        if "-" not in window:
            raise RuntimeError(f"Invalid upload window {window!r}; expected HH:MM-HH:MM")
        start_raw, end_raw = [x.strip() for x in window.split("-", 1)]
        sh, sm = parse_hhmm(start_raw)
        eh, em = parse_hhmm(end_raw)
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if end <= start:
            raise RuntimeError(f"Invalid upload window {window!r}; end must be after start on same day")
        span_minutes = max(0, int((end - start).total_seconds() // 60))
        seed = f"{today}|{window}|{salt}"
        offset = random.Random(seed).randint(0, span_minutes)
        trigger = start + dt.timedelta(minutes=offset)
        active_until = trigger + dt.timedelta(minutes=trigger_window)
        if trigger <= now < active_until:
            return {
                "key": f"{today}:{window}",
                "range": window,
                "trigger_time": trigger.strftime("%Y-%m-%d %H:%M %Z"),
                "active_until": active_until.strftime("%Y-%m-%d %H:%M %Z"),
            }
    return None


def in_upload_slot(now: dt.datetime | None = None, window_minutes: int | None = None) -> bool:
    if upload_windows():
        return randomized_upload_window(now) is not None
    tz = ZoneInfo(os.environ.get("TIKTOK_UPLOAD_TIMEZONE", "Asia/Jakarta"))
    now = (now or now_utc()).astimezone(tz)
    window = window_minutes if window_minutes is not None else int_env("TIKTOK_UPLOAD_SLOT_WINDOW_MINUTES", 20)
    for slot in upload_slots():
        try:
            hour, minute = parse_hhmm(slot)
        except Exception:
            raise RuntimeError(f"Invalid upload slot {slot!r}; expected HH:MM")
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if 0 <= (now - slot_time).total_seconds() <= window * 60:
            return True
    return False


def upload_candidates(rows: list[dict]) -> list[dict]:
    required_status = os.environ.get("TIKTOK_UPLOAD_REQUIRED_STATUS", "READY_TO_UPLOAD").strip().upper()
    candidates = []
    for row in rows:
        if (row.get("status") or "").strip().upper() != required_status:
            continue
        if not (row.get("result_supabase_url") or "").strip():
            continue
        if not (row.get("caption") or "").strip():
            continue
        if (row.get("uploaded_at") or "").strip():
            continue
        candidates.append(row)
    return candidates


def validate_public_video_url(url: str):
    try:
        r = requests.head(url, allow_redirects=True, timeout=20)
        if r.status_code >= 400 or not r.headers.get("content-type"):
            r = requests.get(url, stream=True, allow_redirects=True, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"video url returned HTTP {r.status_code}")
        ctype = (r.headers.get("content-type") or "").lower()
        if ctype and "video" not in ctype and "octet-stream" not in ctype:
            print(f"warning: video url content-type is {ctype!r}", file=sys.stderr)
    except Exception as e:
        raise RuntimeError(f"Could not validate video URL: {e}")


def buffer_channel_id(test: bool = False) -> str:
    if test:
        return require_env("BUFFER_TEST_CHANNEL_ID")
    return os.environ.get("BUFFER_DEFAULT_CHANNEL_ID") or "6a0c2d3d090476fb9936d831"


def buffer_graphql(query: str, variables: dict | None = None, timeout: int = 90) -> dict:
    api_key = require_env("BUFFER_API_KEY")
    resp = requests.post(
        "https://api.buffer.com",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "tiktok-motion-agent-upload-scheduler",
        },
        json={"query": query, "variables": variables or {}},
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text[:2000]}
    if resp.status_code >= 400:
        raise RuntimeError(f"Buffer HTTP {resp.status_code}: {json.dumps(payload, ensure_ascii=False)[:1000]}")
    if payload.get("errors"):
        raise RuntimeError(f"Buffer GraphQL error: {json.dumps(payload['errors'], ensure_ascii=False)[:1000]}")
    return payload


def buffer_create_video_post(channel_id: str, video_url: str, caption: str) -> dict:
    query = """
mutation CreatePost {
  createPost(input: {
    text: %s
    channelId: %s
    schedulingType: automatic
    mode: shareNow
    assets: [{ video: { url: %s } }]
  }) {
    ... on PostActionSuccess {
      post { id text status dueAt sentAt externalLink assets { id source mimeType } }
    }
    ... on MutationError { message }
  }
}
""" % (json.dumps(caption), json.dumps(channel_id), json.dumps(video_url))
    payload = buffer_graphql(query)
    result = ((payload.get("data") or {}).get("createPost") or {})
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post")
    if not post:
        raise RuntimeError(f"Unexpected Buffer response: {json.dumps(payload, ensure_ascii=False)[:1000]}")
    return post


def buffer_get_post(post_id: str) -> dict:
    query = """
query GetPost($id: PostId!) {
  post(input: {id: $id}) {
    id status createdAt updatedAt dueAt sentAt text externalLink channelId channelService
    assets { id source mimeType }
  }
}
"""
    payload = buffer_graphql(query, {"id": post_id}, timeout=45)
    post = ((payload.get("data") or {}).get("post") or {})
    if not post:
        raise RuntimeError(f"Unexpected Buffer post response: {json.dumps(payload, ensure_ascii=False)[:1000]}")
    return post


def buffer_wait_until_posted(post_id: str, timeout_seconds: int | None = None, interval_seconds: int | None = None) -> dict:
    timeout_seconds = timeout_seconds if timeout_seconds is not None else int_env("BUFFER_POST_WAIT_TIMEOUT_SECONDS", 600)
    interval_seconds = interval_seconds if interval_seconds is not None else int_env("BUFFER_POST_WAIT_INTERVAL_SECONDS", 15)
    deadline = time.time() + max(0, timeout_seconds)
    last_post = buffer_get_post(post_id)
    while str(last_post.get("status", "")).lower() in {"sending", "pending", "scheduled", "processing"} and time.time() < deadline:
        time.sleep(max(1, interval_seconds))
        last_post = buffer_get_post(post_id)
    return last_post


def upload_scheduler(dry_run: bool = True, live: bool = False, ignore_slot: bool = False, test_channel: bool = False) -> dict:
    rows = load_run_rows(prefer_sheet=True)
    candidates = upload_candidates(rows)
    random.shuffle(candidates)
    max_per_run = max(1, int_env("TIKTOK_UPLOAD_MAX_PER_RUN", 1))
    picked = candidates[:max_per_run]
    channel_id = buffer_channel_id(test=test_channel)
    active_window = None if ignore_slot else randomized_upload_window()
    state = state_load()
    upload_state = state.setdefault("upload_scheduler", {})
    attempted_windows = set(upload_state.setdefault("attempted_window_keys", []))
    result = {
        "dry_run": dry_run or not live,
        "enabled": truthy_env("TIKTOK_UPLOAD_ENABLED", False),
        "in_slot": in_upload_slot() if not ignore_slot else True,
        "slots": upload_slots(),
        "windows": upload_windows(),
        "active_window": active_window,
        "active_window_already_attempted": bool(active_window and active_window.get("key") in attempted_windows),
        "channel_id": channel_id,
        "candidate_count": len(candidates),
        "picked": [{"job_id": r.get("job_id"), "caption": r.get("caption"), "result_supabase_url": r.get("result_supabase_url")} for r in picked],
        "uploaded": [],
    }
    if dry_run or not live:
        return result
    if not truthy_env("TIKTOK_UPLOAD_ENABLED", False):
        raise RuntimeError("Live upload refused: set TIKTOK_UPLOAD_ENABLED=true")
    if not ignore_slot and not result["in_slot"]:
        raise RuntimeError(f"Live upload refused: current time is outside upload windows {upload_windows() or upload_slots()}")
    if active_window and active_window.get("key") in attempted_windows:
        raise RuntimeError(f"Live upload refused: window already attempted today ({active_window['key']})")

    for row in picked:
        now_pretty = indonesia_pretty_datetime(now_utc())
        attempts = int(row.get("upload_attempts") or 0) + 1
        row.update({
            "status": "UPLOADING",
            "scheduled_at": now_pretty,
            "buffer_channel_id": channel_id,
            "uploaded_via": "buffer",
            "upload_attempts": str(attempts),
            "buffer_error": "",
            "error": "",
        })
        log_row(row)
        if active_window:
            attempted_windows.add(active_window["key"])
            upload_state["attempted_window_keys"] = sorted(attempted_windows)[-60:]
            upload_state["last_attempted_window"] = active_window
            state_save(state)
        try:
            video_url = row["result_supabase_url"].strip()
            validate_public_video_url(video_url)
            post = buffer_create_video_post(channel_id, video_url, row["caption"].strip())
            if truthy_env("TIKTOK_UPLOAD_WAIT_FOR_BUFFER_SENT", True) and post.get("id"):
                post = buffer_wait_until_posted(post["id"])
            row.update({
                "status": "UPLOADED",
                "uploaded_at": indonesia_pretty_datetime(now_utc()),
                "buffer_post_id": post.get("id", ""),
                "buffer_status": post.get("status", ""),
                "external_link": post.get("externalLink", ""),
                "buffer_error": "",
                "error": "",
            })
            log_row(row)
            result["uploaded"].append({
                "job_id": row.get("job_id"),
                "buffer_post_id": row.get("buffer_post_id"),
                "buffer_status": row.get("buffer_status"),
                "external_link": row.get("external_link"),
            })
        except Exception as e:
            msg = str(e)[:1000]
            row.update({"status": "UPLOAD_FAILED", "buffer_error": msg, "error": msg})
            log_row(row)
            raise
    return result
