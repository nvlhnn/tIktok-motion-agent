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
from .utils import now_utc, indonesia_pretty_datetime, parse_hhmm, parse_indonesia_pretty_datetime
from .state import state_load, state_save
from .storage import log_row, load_run_rows
from .affiliate_links import (
    affiliate_state_for_row,
    comment_affiliate_for_row,
    load_affiliate_links,
    upsert_affiliate_link,
)


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


def buffer_channel_ids(test: bool = False) -> list[str]:
    """Return Buffer upload targets in order: primary channel, then extras.

    Keep the first channel as the canonical/primary target because downstream TikTok
    affiliate/stat checks read `external_link` from the first successful post.
    """
    if test:
        return [buffer_channel_id(test=True)]
    raw_ids = [buffer_channel_id(test=False)]
    extra_raw = os.environ.get("BUFFER_EXTRA_CHANNEL_IDS", "")
    raw_ids.extend(s.strip() for s in re.split(r"[,\n]", extra_raw) if s.strip())
    seen = set()
    ids = []
    for channel_id in raw_ids:
        if channel_id in seen:
            continue
        seen.add(channel_id)
        ids.append(channel_id)
    return ids


def buffer_post_metadata_literal(channel_id: str) -> str:
    """Return Buffer GraphQL metadata for channel-specific post types.

    Buffer requires explicit type metadata for Facebook Page posts, and Instagram
    video uploads should also be explicit. Default to regular feed `post`, not
    `reel`, because generated videos may be below Reels' 540x960 minimum.
    """
    facebook_id = os.environ.get("BUFFER_FACEBOOK_CHANNEL_ID", "").strip()
    instagram_id = os.environ.get("BUFFER_INSTAGRAM_CHANNEL_ID", "").strip()
    if facebook_id and channel_id == facebook_id:
        post_type = os.environ.get("BUFFER_FACEBOOK_POST_TYPE", "post").strip() or "post"
        if post_type not in {"post", "story", "reel"}:
            raise RuntimeError(f"Invalid BUFFER_FACEBOOK_POST_TYPE={post_type!r}; expected post, story, or reel")
        return f"metadata: {{ facebook: {{ type: {post_type} }} }}"
    if instagram_id and channel_id == instagram_id:
        post_type = os.environ.get("BUFFER_INSTAGRAM_POST_TYPE", "post").strip() or "post"
        if post_type not in {"post", "story", "reel"}:
            raise RuntimeError(f"Invalid BUFFER_INSTAGRAM_POST_TYPE={post_type!r}; expected post, story, or reel")
        should_share = "true" if truthy_env("BUFFER_INSTAGRAM_SHARE_TO_FEED", True) else "false"
        return f"metadata: {{ instagram: {{ type: {post_type}, shouldShareToFeed: {should_share} }} }}"
    return ""


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
    return buffer_create_video_post_at(channel_id, video_url, caption, due_at=None)


def buffer_create_video_post_at(channel_id: str, video_url: str, caption: str, due_at: dt.datetime | None = None) -> dict:
    metadata = buffer_post_metadata_literal(channel_id)
    metadata_line = f"    {metadata}\n" if metadata else ""
    if due_at:
        due_at_utc = due_at.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        mode_line = "    mode: customScheduled\n"
        due_at_line = f"    dueAt: {json.dumps(due_at_utc)}\n"
    else:
        mode_line = "    mode: shareNow\n"
        due_at_line = ""
    query = """
mutation CreatePost {
  createPost(input: {
    text: %s
    channelId: %s
    schedulingType: automatic
%s%s
%s
    assets: [{ video: { url: %s } }]
  }) {
    ... on PostActionSuccess {
      post { id text status dueAt sentAt externalLink assets { id source mimeType } }
    }
    ... on MutationError { message }
  }
}
""" % (json.dumps(caption), json.dumps(channel_id), mode_line, due_at_line, metadata_line, json.dumps(video_url))
    payload = buffer_graphql(query)
    result = ((payload.get("data") or {}).get("createPost") or {})
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post")
    if not post:
        raise RuntimeError(f"Unexpected Buffer response: {json.dumps(payload, ensure_ascii=False)[:1000]}")
    return post


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TIKTOK_UPLOAD_TIMEZONE", "Asia/Jakarta"))


def _parse_window_for_date(window: str, base: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    if "-" not in window:
        raise RuntimeError(f"Invalid upload window {window!r}; expected HH:MM-HH:MM")
    start_raw, end_raw = [x.strip() for x in window.split("-", 1)]
    sh, sm = parse_hhmm(start_raw)
    eh, em = parse_hhmm(end_raw)
    start = base.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = base.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        raise RuntimeError(f"Invalid upload window {window!r}; end must be after start on same day")
    return start, end


def daily_schedule_times(count: int | None = None, now: dt.datetime | None = None) -> list[dict]:
    tz = _local_tz()
    now = (now or now_utc()).astimezone(tz)
    days_ahead = int_env("TIKTOK_DAILY_SCHEDULE_DAYS_AHEAD", 1)
    schedule_base = now + dt.timedelta(days=days_ahead)
    windows = upload_windows()
    if not windows:
        slots = upload_slots()
        count = count or len(slots)
        times = []
        for slot in slots[:count]:
            h, m = parse_hhmm(slot)
            due = schedule_base.replace(hour=h, minute=m, second=0, microsecond=0)
            times.append({"range": slot, "due_at": due, "due_at_local": due.strftime("%Y-%m-%d %H:%M %Z")})
        return times
    count = count or len(windows)
    salt = os.environ.get("TIKTOK_UPLOAD_RANDOM_SALT") or buffer_channel_id(test=False)
    today = now.strftime("%Y-%m-%d")
    times = []
    for idx, window in enumerate(windows[:count]):
        start, end = _parse_window_for_date(window, schedule_base)
        latest = end - dt.timedelta(minutes=1)
        span_minutes = max(0, int((latest - start).total_seconds() // 60))
        seed = f"schedule|{today}|{window}|{idx}|{salt}"
        due = start + dt.timedelta(minutes=random.Random(seed).randint(0, span_minutes))
        times.append({"range": window, "due_at": due, "due_at_local": due.strftime("%Y-%m-%d %H:%M %Z")})
    return times


def _posts_from_row(row: dict) -> list[dict]:
    ids = [s.strip() for s in str(row.get("buffer_post_id") or "").split(",") if s.strip()]
    channel_ids = [s.strip() for s in str(row.get("buffer_channel_id") or "").split(",") if s.strip()]
    posts = []
    for idx, post_id in enumerate(ids):
        post = buffer_get_post(post_id)
        post["channel_id"] = channel_ids[idx] if idx < len(channel_ids) else post.get("channelId", "")
        posts.append(post)
    return posts


def schedule_daily_uploads(dry_run: bool = True, live: bool = False, count: int | None = None, test_channel: bool = False) -> dict:
    rows = load_run_rows(prefer_sheet=True)
    candidates = upload_candidates(rows)
    random.shuffle(candidates)
    now_local = now_utc().astimezone(_local_tz())
    min_lead = dt.timedelta(minutes=int_env("TIKTOK_DAILY_SCHEDULE_MIN_LEAD_MINUTES", 15))
    times = [t for t in daily_schedule_times(count=count) if t["due_at"] > now_local + min_lead]
    count = min(len(times), len(candidates), max(1, int_env("TIKTOK_DAILY_SCHEDULE_COUNT", len(times) or 4)))
    picked = candidates[:count]
    channel_ids = buffer_channel_ids(test=test_channel)
    today = now_utc().astimezone(_local_tz()).strftime("%Y-%m-%d")
    target_date = times[0]["due_at"].astimezone(_local_tz()).strftime("%Y-%m-%d") if times else ""
    affiliate_rows = load_affiliate_links(prefer_sheet=True)
    result = {
        "dry_run": dry_run or not live,
        "enabled": truthy_env("TIKTOK_UPLOAD_ENABLED", False),
        "date": today,
        "target_date": target_date,
        "channel_ids": channel_ids,
        "candidate_count": len(candidates),
        "scheduled": [],
        "affiliate_followups": [],
    }
    state = state_load()
    upload_state = state.setdefault("upload_scheduler", {})
    if upload_state.get("daily_schedule_date") == today:
        result["skipped"] = "daily_schedule_already_created"
        result["existing"] = upload_state.get("daily_schedule", [])
        return result
    for row, scheduled in zip(picked, times[:count]):
        aff = affiliate_state_for_row(row, affiliate_rows)
        item = {
            "job_id": row.get("job_id"),
            "due_at": scheduled["due_at"].astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "due_at_local": scheduled["due_at_local"],
            "window": scheduled["range"],
            "caption": row.get("caption"),
            "result_supabase_url": row.get("result_supabase_url"),
            "product_key": aff.get("product_key"),
            "affiliate_status": aff.get("affiliate_status"),
        }
        result["scheduled"].append(item)
        if aff.get("affiliate_status") == "MISSING":
            result["affiliate_followups"].append({
                "job_id": row.get("job_id"),
                "product_key": aff.get("product_key"),
                "product_url": row.get("product_url"),
                "product_title": row.get("product_title"),
                "action_needed": "Send Shopee affiliate link; scheduling would still continue in live mode.",
            })
    if dry_run or not live:
        return result
    if not truthy_env("TIKTOK_UPLOAD_ENABLED", False):
        raise RuntimeError("Daily schedule refused: set TIKTOK_UPLOAD_ENABLED=true")
    result["affiliate_followups"] = []
    created = []
    for row, scheduled in zip(picked, times[:count]):
        attempts = int(row.get("upload_attempts") or 0) + 1
        video_url = row["result_supabase_url"].strip()
        validate_public_video_url(video_url)
        posts = []
        for target_channel_id in channel_ids:
            post = buffer_create_video_post_at(target_channel_id, video_url, row["caption"].strip(), due_at=scheduled["due_at"])
            post["channel_id"] = target_channel_id
            posts.append(post)
        aff = affiliate_state_for_row(row, affiliate_rows)
        if aff.get("affiliate_status") == "MISSING" and aff.get("product_key"):
            upsert_affiliate_link(row.get("product_url", ""), product_name=row.get("product_title", ""), notes="Created by Buffer scheduler; waiting for Shopee affiliate link")
        row.update({
            "status": "SCHEDULED_UPLOAD",
            "scheduled_at": scheduled["due_at_local"],
            "buffer_channel_id": ",".join(channel_ids),
            "buffer_post_id": ",".join(p.get("id", "") for p in posts if p.get("id")),
            "buffer_status": ",".join(p.get("status", "") for p in posts if p.get("status")),
            "uploaded_via": "buffer",
            "upload_attempts": str(attempts),
            "product_key": aff.get("product_key", ""),
            "affiliate_status": aff.get("affiliate_status", ""),
            "shopee_affiliate_url": aff.get("shopee_affiliate_url", ""),
            "fb_comment_status": "PENDING_POST" if aff.get("affiliate_status") == "FOUND" else "PENDING_LINK",
            "ig_comment_status": "PENDING_POST" if aff.get("affiliate_status") == "FOUND" else "PENDING_LINK",
            "action_needed": aff.get("action_needed", ""),
            "buffer_error": "",
            "error": "",
        })
        log_row(row)
        created_item = {
            "job_id": row.get("job_id"),
            "due_at_local": scheduled["due_at_local"],
            "window": scheduled["range"],
            "buffer_post_id": row.get("buffer_post_id"),
            "buffer_status": row.get("buffer_status"),
            "product_url": row.get("product_url"),
            "product_key": row.get("product_key"),
            "affiliate_status": row.get("affiliate_status"),
            "shopee_affiliate_url": row.get("shopee_affiliate_url"),
            "product_image_url": row.get("product_image_url"),
            "caption": row.get("caption"),
        }
        created.append(created_item)
        if aff.get("affiliate_status") == "MISSING":
            result["affiliate_followups"].append({
                "job_id": row.get("job_id"),
                "product_key": aff.get("product_key"),
                "product_url": row.get("product_url"),
                "product_title": row.get("product_title"),
                "action_needed": "Send Shopee affiliate link; Buffer schedule was created and is not blocked.",
            })
    upload_state["daily_schedule_date"] = today
    upload_state["daily_schedule"] = created
    state_save(state)
    result["scheduled"] = created
    return result


def scheduled_upload_check_due_at(row: dict, now: dt.datetime | None = None) -> dt.datetime | None:
    """Return when this row may be checked against Buffer.

    Buffer has a small daily API quota, so don't poll scheduled posts constantly.
    If the scheduled time falls inside a configured upload window, wait until the
    end of that window (plus a small grace period) before the first Buffer hit.
    Otherwise, wait until the scheduled time plus the same grace period.
    """
    scheduled = parse_indonesia_pretty_datetime(row.get("scheduled_at") or "")
    if not scheduled:
        return None
    tz = _local_tz()
    scheduled = scheduled.astimezone(tz)
    grace = dt.timedelta(minutes=int_env("TIKTOK_SCHEDULED_CHECK_GRACE_MINUTES", 5))
    for window in upload_windows():
        try:
            start, end = _parse_window_for_date(window, scheduled)
        except Exception:
            continue
        if start <= scheduled <= end:
            return end + grace
    return scheduled + grace


def scheduled_upload_ready_to_check(row: dict, now: dt.datetime | None = None) -> bool:
    due_at = scheduled_upload_check_due_at(row, now=now)
    if not due_at:
        return False
    tz = _local_tz()
    current = (now or now_utc()).astimezone(tz)
    return current >= due_at.astimezone(tz)


def check_scheduled_uploads(dry_run: bool = True, live: bool = False) -> dict:
    rows = load_run_rows(prefer_sheet=True)
    pending_all = [r for r in rows if (r.get("status") or "").strip().upper() in {"SCHEDULED_UPLOAD", "UPLOADING"} and (r.get("buffer_post_id") or "").strip() and not (r.get("uploaded_at") or "").strip()]
    now = now_utc()
    pending = [r for r in pending_all if scheduled_upload_ready_to_check(r, now=now)]
    skipped_waiting = []
    for row in pending_all:
        if row in pending:
            continue
        due_at = scheduled_upload_check_due_at(row, now=now)
        skipped_waiting.append({
            "job_id": row.get("job_id"),
            "scheduled_at": row.get("scheduled_at"),
            "check_due_at": due_at.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M %Z") if due_at else "",
        })
    result = {"dry_run": dry_run or not live, "pending_count": len(pending), "waiting_count": len(skipped_waiting), "waiting": skipped_waiting, "checked": [], "uploaded": [], "failed": [], "affiliate_comments": []}
    for row in pending:
        try:
            posts = _posts_from_row(row)
        except Exception as e:
            result["checked"].append({"job_id": row.get("job_id"), "error": str(e)[:500]})
            continue
        primary_post = posts[0] if posts else {}
        post_links = post_external_links_by_service(posts)
        failed_posts = [p for p in posts if str(p.get("status", "")).lower() == "error"]
        primary_status = str(primary_post.get("status", "")).lower()
        pending_statuses = {"sending", "pending", "scheduled", "processing"}
        has_pending_posts = any(str(p.get("status", "")).lower() in pending_statuses for p in posts)
        item = {
            "job_id": row.get("job_id"),
            "buffer_post_id": row.get("buffer_post_id"),
            "buffer_status": ",".join(p.get("status", "") for p in posts if p.get("status")),
            "post_urls": post_links,
        }
        result["checked"].append(item)
        if dry_run or not live:
            continue
        if primary_status == "sent" and not has_pending_posts:
            buffer_error = ""
            if failed_posts:
                parts = []
                for p in failed_posts:
                    err = p.get("error") or {}
                    msg = err.get("message") or err.get("rawError") or "unknown Buffer publishing error"
                    parts.append(f"{p.get('channelService') or p.get('channel_id')}: {msg}")
                buffer_error = "partial Buffer scheduled upload failure: " + "; ".join(parts)
            row.update({
                "status": "UPLOADED",
                "uploaded_at": indonesia_pretty_datetime(now_utc()),
                "buffer_status": item["buffer_status"],
                "external_link": primary_post.get("externalLink", ""),
                "tiktok_post_url": post_links.get("tiktok", ""),
                "facebook_post_url": post_links.get("facebook", ""),
                "instagram_post_url": post_links.get("instagram", ""),
                "post_external_links": "\n".join(f"{service}: {link}" for service, link in post_links.items()),
                "buffer_error": buffer_error,
                "error": "",
            })
            log_row(row)
            try:
                _, affiliate_comment_result = comment_affiliate_for_row(row, live=live, prefer_sheet=True)
                result["affiliate_comments"].append(affiliate_comment_result)
            except Exception as e:
                result["affiliate_comments"].append({"job_id": row.get("job_id"), "error": str(e)[:500], "non_blocking": True})
            uploaded = dict(item)
            uploaded.update({
                "product_url": row.get("product_url"),
                "product_image_url": row.get("product_image_url"),
                "uploaded_at": row.get("uploaded_at"),
                "buffer_error": row.get("buffer_error"),
                "caption": row.get("caption"),
                "posts": [
                    {
                        "channel_id": p.get("channel_id") or p.get("channelId"),
                        "channel_service": p.get("channelService"),
                        "buffer_post_id": p.get("id"),
                        "buffer_status": p.get("status"),
                        "external_link": p.get("externalLink"),
                        "error": (p.get("error") or {}).get("message"),
                    }
                    for p in posts
                ],
            })
            result["uploaded"].append(uploaded)
        elif primary_status == "error" or (failed_posts and not has_pending_posts):
            parts = []
            for p in failed_posts:
                err = p.get("error") or {}
                msg = err.get("message") or err.get("rawError") or "unknown Buffer publishing error"
                parts.append(f"{p.get('channelService') or p.get('channel_id')}: {msg}")
            buffer_error = "Buffer scheduled upload failure: " + "; ".join(parts)
            row.update({"status": "UPLOAD_FAILED", "buffer_status": item["buffer_status"], "buffer_error": buffer_error, "error": buffer_error})
            log_row(row)
            failed = dict(item)
            failed["buffer_error"] = buffer_error
            result["failed"].append(failed)
    return result


def buffer_get_post(post_id: str) -> dict:
    query = """
query GetPost($id: PostId!) {
  post(input: {id: $id}) {
    id status createdAt updatedAt dueAt sentAt text externalLink channelId channelService
    error { message rawError supportUrl }
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


def post_external_links_by_service(posts: list[dict]) -> dict[str, str]:
    links = {}
    for post in posts:
        service = str(post.get("channelService") or "").strip().lower()
        link = str(post.get("externalLink") or "").strip()
        if service and link and service not in links:
            links[service] = link
    return links


def upload_scheduler(dry_run: bool = True, live: bool = False, ignore_slot: bool = False, test_channel: bool = False) -> dict:
    rows = load_run_rows(prefer_sheet=True)
    candidates = upload_candidates(rows)
    random.shuffle(candidates)
    max_per_run = max(1, int_env("TIKTOK_UPLOAD_MAX_PER_RUN", 1))
    picked = candidates[:max_per_run]
    channel_ids = buffer_channel_ids(test=test_channel)
    channel_id = channel_ids[0]
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
        "channel_ids": channel_ids,
        "candidate_count": len(candidates),
        "picked": [{"job_id": r.get("job_id"), "caption": r.get("caption"), "result_supabase_url": r.get("result_supabase_url")} for r in picked],
        "uploaded": [],
        "affiliate_followups": [],
        "affiliate_comments": [],
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
        aff = affiliate_state_for_row(row, load_affiliate_links(prefer_sheet=True))
        if aff.get("affiliate_status") == "MISSING" and aff.get("product_key"):
            upsert_affiliate_link(row.get("product_url", ""), product_name=row.get("product_title", ""), notes="Created by Buffer uploader; waiting for Shopee affiliate link")
        row.update({
            "status": "UPLOADING",
            "scheduled_at": now_pretty,
            "buffer_channel_id": ",".join(channel_ids),
            "uploaded_via": "buffer",
            "upload_attempts": str(attempts),
            "product_key": aff.get("product_key", ""),
            "affiliate_status": aff.get("affiliate_status", ""),
            "shopee_affiliate_url": aff.get("shopee_affiliate_url", ""),
            "fb_comment_status": "PENDING_POST" if aff.get("affiliate_status") == "FOUND" else "PENDING_LINK",
            "ig_comment_status": "PENDING_POST" if aff.get("affiliate_status") == "FOUND" else "PENDING_LINK",
            "action_needed": aff.get("action_needed", ""),
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
            posts = []
            for target_channel_id in channel_ids:
                post = buffer_create_video_post(target_channel_id, video_url, row["caption"].strip())
                if truthy_env("TIKTOK_UPLOAD_WAIT_FOR_BUFFER_SENT", True) and post.get("id"):
                    post = buffer_wait_until_posted(post["id"])
                post["channel_id"] = target_channel_id
                posts.append(post)
            primary_post = posts[0] if posts else {}
            post_links = post_external_links_by_service(posts)
            failed_posts = [p for p in posts if str(p.get("status", "")).lower() == "error"]
            primary_failed = primary_post and str(primary_post.get("status", "")).lower() == "error"
            buffer_error = ""
            if failed_posts:
                parts = []
                for p in failed_posts:
                    err = p.get("error") or {}
                    msg = err.get("message") or err.get("rawError") or "unknown Buffer publishing error"
                    parts.append(f"{p.get('channelService') or p.get('channel_id')}: {msg}")
                buffer_error = "partial Buffer upload failure: " + "; ".join(parts)
            row.update({
                "status": "UPLOAD_FAILED" if primary_failed else "UPLOADED",
                "uploaded_at": indonesia_pretty_datetime(now_utc()),
                "buffer_post_id": ",".join(p.get("id", "") for p in posts if p.get("id")),
                "buffer_status": ",".join(p.get("status", "") for p in posts if p.get("status")),
                "external_link": primary_post.get("externalLink", ""),
                "tiktok_post_url": post_links.get("tiktok", ""),
                "facebook_post_url": post_links.get("facebook", ""),
                "instagram_post_url": post_links.get("instagram", ""),
                "post_external_links": "\n".join(f"{service}: {link}" for service, link in post_links.items()),
                "buffer_error": buffer_error,
                "error": buffer_error if primary_failed else "",
            })
            log_row(row)
            try:
                _, affiliate_comment_result = comment_affiliate_for_row(row, live=live, prefer_sheet=True)
                result["affiliate_comments"].append(affiliate_comment_result)
                if affiliate_comment_result.get("needs_affiliate_link"):
                    result["affiliate_followups"].append({
                        "job_id": row.get("job_id"),
                        "product_key": row.get("product_key"),
                        "product_url": row.get("product_url"),
                        "product_title": row.get("product_title"),
                        "action_needed": "Send Shopee affiliate link; upload was not blocked.",
                    })
            except Exception as e:
                result["affiliate_comments"].append({"job_id": row.get("job_id"), "error": str(e)[:500], "non_blocking": True})
            if primary_failed:
                raise RuntimeError(buffer_error or "Primary Buffer upload failed")
            result["uploaded"].append({
                "job_id": row.get("job_id"),
                "product_url": row.get("product_url"),
                "product_image_url": row.get("product_image_url"),
                "buffer_post_id": row.get("buffer_post_id"),
                "buffer_status": row.get("buffer_status"),
                "external_link": row.get("external_link"),
                "post_urls": post_links,
                "buffer_error": row.get("buffer_error"),
                "posts": [
                    {
                        "channel_id": p.get("channel_id"),
                        "channel_service": p.get("channelService"),
                        "buffer_post_id": p.get("id"),
                        "buffer_status": p.get("status"),
                        "external_link": p.get("externalLink"),
                        "error": (p.get("error") or {}).get("message"),
                    }
                    for p in posts
                ],
            })
        except Exception as e:
            msg = str(e)[:1000]
            row.update({"status": "UPLOAD_FAILED", "buffer_error": msg, "error": msg})
            log_row(row)
            raise
    return result
