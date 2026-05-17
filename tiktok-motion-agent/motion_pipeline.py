#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests

try:
    import gspread
except Exception:  # pragma: no cover
    gspread = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOWNLOADS_DIR = ROOT / "downloads"
LOGS_DIR = ROOT / "logs"
STATE_PATH = DATA_DIR / "state.json"
RUNS_CSV = DATA_DIR / "runs.csv"

DEFAULT_PROMPT = (
    "Transfer the body movement and camera rhythm from the TikTok reference video to the person in the reference image. "
    "Preserve the person identity, outfit, pose style, and background as much as possible. "
    "Keep the motion natural, realistic, vertical social-video style, with stable identity and clean lighting."
)

COLUMNS = [
    "created_at",
    "job_id",
    "status",
    "input_image_url",
    "source_tiktok_url",
    "source_video_url",
    "source_product_url",
    "source_product_title",
    "magnific_task_id",
    "result_magnific_url",
    "result_supabase_url",
    "delete_after",
    "error",
]


def load_env(path: Path = ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime):
    return ts.replace(microsecond=0).isoformat()


def safe_name(s: str):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return s[:160] or "file"


def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env: {name}")
    return v


def state_load():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"recent_video_ids": [], "jobs": []}


def state_save(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def get_sheet():
    if gspread is None:
        raise RuntimeError("gspread is not installed")
    cred_path = require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = require_env("SPREADSHEET_ID")
    gc = gspread.service_account(filename=cred_path)
    return gc.open_by_key(spreadsheet_id).sheet1


def ensure_sheet_header(ws):
    existing = ws.row_values(1)
    if existing != COLUMNS:
        if not existing:
            ws.append_row(COLUMNS, value_input_option="RAW")
        else:
            # Keep existing content but make header usable.
            ws.update("A1", [COLUMNS])


def append_sheet(row: dict):
    ws = get_sheet()
    ensure_sheet_header(ws)
    ws.append_row([row.get(c, "") for c in COLUMNS], value_input_option="RAW")


def append_local_csv(row: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in COLUMNS})


def log_row(row: dict):
    append_local_csv(row)
    append_sheet(row)


def yt_dlp_entries(limit=150):
    profile = require_env("TIKTOK_PROFILE_URL")
    yt = "/root/.openclaw/workspace/.venv-ytdlp/bin/yt-dlp"
    cmd = [yt, "--flat-playlist", "--dump-json", profile]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    entries = []
    assert p.stdout is not None
    for line in p.stdout:
        try:
            obj = json.loads(line)
            if obj.get("id"):
                entries.append(obj)
        except Exception:
            pass
        if len(entries) >= limit:
            p.kill()
            break
    return entries


def pick_video(state):
    entries = yt_dlp_entries()
    if not entries:
        raise RuntimeError("Could not list TikTok profile videos")
    avoid_count = int(os.environ.get("RECENT_VIDEO_AVOID_COUNT", "10"))
    recent = set(state.get("recent_video_ids", [])[-avoid_count:])
    candidates = [e for e in entries if e.get("id") not in recent] or entries
    return random.choice(candidates)


def get_tikwm_data(tiktok_url: str):
    r = requests.get("https://www.tikwm.com/api/", params={"url": tiktok_url}, timeout=45)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"tikwm failed: {j}")
    return j.get("data") or {}


def extract_product_from_html(tiktok_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    }
    try:
        text = requests.get(tiktok_url, headers=headers, timeout=45).text
    except Exception:
        return "", ""
    # Product info often appears as escaped JSON inside the HTML.
    idx = text.find("product_id")
    if idx == -1:
        return "", ""
    window = text[max(0, idx - 6000): idx + 16000]
    # Repeated unescape helps nested JSON strings.
    for _ in range(3):
        window = window.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        window = urllib.parse.unquote(window)
    title = ""
    product_id = ""
    seo_url = ""
    m = re.search(r'"title"\s*:\s*"([^"]+)"', window)
    if m:
        title = m.group(1)
    m = re.search(r'"product_id"\s*:?\s*"?(\d{8,})"?', window)
    if m:
        product_id = m.group(1)
    m = re.search(r'"seo_url"\s*:\s*"(https?://[^"\\]+)', window)
    if m:
        seo_url = m.group(1).replace("\\/", "/")
    if not seo_url and product_id:
        seo_url = f"https://shop-id.tokopedia.com/pdp/{product_id}"
    return seo_url, title


def download_tiktok_video(video_id: str, tiktok_url: str, job_dir: Path):
    data = get_tikwm_data(tiktok_url)
    play = data.get("play") or data.get("wmplay")
    if not play:
        raise RuntimeError("No downloadable video URL found")
    path = job_dir / f"keranjang_tiktok08_{video_id}.mp4"
    with requests.get(play, stream=True, timeout=120, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024 * 256):
                if chunk:
                    f.write(chunk)
    if path.stat().st_size < 200 * 1024:
        raise RuntimeError("Downloaded TikTok video is suspiciously small")
    product_url, product_title = extract_product_from_html(tiktok_url)
    return path, data, product_url, product_title


def supabase_upload(local_path: Path, object_path: str):
    project = require_env("SUPABASE_PROJECT_REF")
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    subprocess.run([
        "npx", "supabase", "--experimental", "storage", "cp", str(local_path),
        f"ss:///{bucket}/{object_path}",
        "--content-type", mime,
        "--cache-control", "max-age=86400",
        "--linked",
    ], cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return f"https://{project}.supabase.co/storage/v1/object/public/{urllib.parse.quote(bucket)}/{urllib.parse.quote(object_path, safe='/')}"


def magnific_post(payload: dict):
    project = require_env("SUPABASE_PROJECT_REF")
    key = require_env("MAGNIFIC_API_KEY")
    url = f"https://{project}.supabase.co/functions/v1/magnific-motion"
    r = requests.post(url, json=payload, headers={"x-magnific-api-key": key, "Accept": "application/json"}, timeout=120)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if not r.ok:
        raise RuntimeError(f"Magnific function failed {r.status_code}: {data}")
    return data

def download_url(url: str, path: Path):
    with requests.get(url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024 * 256):
                if chunk:
                    f.write(chunk)


def supabase_rm_prefix(prefix: str, dry_run: bool = False):
    """Delete a Supabase Storage prefix from the configured public bucket."""
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    prefix = prefix.strip("/")
    if not prefix:
        raise RuntimeError("Refusing to delete empty Supabase prefix")
    target = f"ss:///{bucket}/{prefix}"
    if dry_run:
        return {"target": target, "dry_run": True}
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    subprocess.run([
        "npx", "supabase", "--experimental", "storage", "rm", "-r", target, "--linked",
    ], cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"target": target, "dry_run": False}


def cleanup_old(dry_run: bool = False):
    retention_days = int(os.environ.get("RETENTION_DAYS", "7"))
    cutoff = time.time() - retention_days * 86400
    removed_local = []
    removed_supabase = []

    # Local cleanup.
    for base in [DOWNLOADS_DIR, LOGS_DIR]:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                removed_local.append(str(p))
                if not dry_run:
                    p.unlink(missing_ok=True)
        if not dry_run:
            for d in sorted([x for x in base.rglob("*") if x.is_dir()], reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass

    # Remote Supabase cleanup. Jobs are recorded with a precise prefix like
    # magnific/automation/<job_id>/ so we only delete automation-owned assets.
    state = state_load()
    jobs = state.get("jobs", [])
    kept_jobs = []
    now = now_utc()
    for job in jobs:
        delete_after = job.get("delete_after")
        prefix = job.get("supabase_prefix")
        expired = False
        if delete_after:
            try:
                expired = dt.datetime.fromisoformat(delete_after).astimezone(dt.timezone.utc) <= now
            except Exception:
                expired = False
        if expired and prefix:
            removed_supabase.append(supabase_rm_prefix(prefix, dry_run=dry_run))
            if dry_run:
                kept_jobs.append(job)
        else:
            kept_jobs.append(job)
    if not dry_run and kept_jobs != jobs:
        state["jobs"] = kept_jobs
        state_save(state)

    return {"removed_local": removed_local, "removed_supabase": removed_supabase}


def run(image_path: str):
    load_env()
    DATA_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    cleanup_old()

    src_image = Path(image_path).expanduser().resolve()
    if not src_image.exists():
        raise RuntimeError(f"Image not found: {src_image}")

    created = now_utc()
    job_id = created.strftime("%Y%m%d%H%M%S") + "-" + os.urandom(3).hex()
    delete_after = iso(created + dt.timedelta(days=int(os.environ.get("RETENTION_DAYS", "7"))))
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state = state_load()

    row = {"created_at": iso(created), "job_id": job_id, "status": "STARTED", "delete_after": delete_after}
    try:
        entry = pick_video(state)
        video_id = entry["id"]
        tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{video_id}"
        row["source_tiktok_url"] = tiktok_url

        local_video, tikwm_data, product_url, product_title = download_tiktok_video(video_id, tiktok_url, job_dir)
        row["source_product_url"] = product_url
        row["source_product_title"] = product_title

        image_ext = src_image.suffix.lower() or ".png"
        image_copy = job_dir / f"reference{image_ext}"
        shutil.copy2(src_image, image_copy)

        image_obj = f"magnific/automation/{job_id}/reference{image_ext}"
        video_obj = f"magnific/automation/{job_id}/source_{video_id}.mp4"
        row["input_image_url"] = supabase_upload(image_copy, image_obj)
        row["source_video_url"] = supabase_upload(local_video, video_obj)

        gen = magnific_post({
            "action": "generate",
            "image_url": row["input_image_url"],
            "video_url": row["source_video_url"],
            "character_orientation": "video",
            "cfg_scale": 0.5,
            "prompt": DEFAULT_PROMPT,
        })
        task_id = (gen.get("data") or {}).get("task_id")
        row["magnific_task_id"] = task_id or ""
        if not task_id:
            raise RuntimeError(f"No task_id from Magnific: {gen}")

        while True:
            time.sleep(180)
            status = magnific_post({"action": "status", "task_id": task_id})
            d = status.get("data") or status
            state_value = d.get("status")
            if state_value == "COMPLETED":
                generated = d.get("generated") or []
                if not generated:
                    raise RuntimeError(f"Completed but no generated URL: {status}")
                row["result_magnific_url"] = generated[0]
                result_path = job_dir / f"result_{task_id}.mp4"
                download_url(generated[0], result_path)
                result_obj = f"magnific/automation/{job_id}/result_{task_id}.mp4"
                row["result_supabase_url"] = supabase_upload(result_path, result_obj)
                row["status"] = "COMPLETED"
                break
            if state_value in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}:
                raise RuntimeError(f"Magnific ended with {state_value}: {status}")

        recent = state.setdefault("recent_video_ids", [])
        recent.append(video_id)
        state["recent_video_ids"] = recent[-100:]
        state.setdefault("jobs", []).append({"job_id": job_id, "created_at": row["created_at"], "delete_after": delete_after, "supabase_prefix": f"magnific/automation/{job_id}/"})
        state_save(state)
        log_row(row)
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        row["status"] = "FAILED"
        row["error"] = str(e)
        try:
            log_row(row)
        except Exception as e2:
            print(f"sheet/log failed too: {e2}", file=sys.stderr)
        print(json.dumps(row, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run")
    runp.add_argument("image")
    cleanp = sub.add_parser("cleanup")
    cleanp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    if args.cmd == "run":
        raise SystemExit(run(args.image))
    if args.cmd == "cleanup":
        removed = cleanup_old(dry_run=args.dry_run)
        print(json.dumps(removed, indent=2))


if __name__ == "__main__":
    main()
