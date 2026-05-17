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

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

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
    "product_video_url",
    "product_url",
    "product_title",
    "product_image_url",
    "input_image_url",
    "motion_tiktok_video_url",
    "motion_supabase_video_url",
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
        # Clear stale headers to the right when the schema shrinks/renames columns.
        clear_width = max(len(existing), len(COLUMNS), 26)
        values = COLUMNS + [""] * (clear_width - len(COLUMNS))
        end_col = chr(ord("A") + clear_width - 1) if clear_width <= 26 else "Z"
        ws.update(f"A1:{end_col}1", [values[:clear_width]])


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


def upsert_local_csv(row: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    replaced = False
    if RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
    for existing in rows:
        if existing.get("job_id") == row.get("job_id"):
            existing.update({c: row.get(c, "") for c in COLUMNS})
            replaced = True
            break
    if not replaced:
        rows.append({c: row.get(c, "") for c in COLUMNS})
    with RUNS_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows([{c: r.get(c, "") for c in COLUMNS} for r in rows])


def upsert_sheet(row: dict):
    ws = get_sheet()
    ensure_sheet_header(ws)
    values = [row.get(c, "") for c in COLUMNS]
    job_id = row.get("job_id")
    row_index = None
    if job_id:
        for idx, value in enumerate(ws.col_values(COLUMNS.index("job_id") + 1), start=1):
            if idx > 1 and value == job_id:
                row_index = idx
                break
    if row_index:
        end_col = chr(ord("A") + len(COLUMNS) - 1)
        ws.update(f"A{row_index}:{end_col}{row_index}", [values])
    else:
        ws.append_row(values, value_input_option="RAW")


def log_row(row: dict):
    upsert_local_csv(row)
    upsert_sheet(row)


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


def video_candidates(state):
    entries = yt_dlp_entries()
    if not entries:
        raise RuntimeError("Could not list TikTok profile videos")
    avoid_count = int(os.environ.get("RECENT_VIDEO_AVOID_COUNT", "10"))
    recent = set(state.get("recent_video_ids", [])[-avoid_count:])
    candidates = [e for e in entries if e.get("id") not in recent] or entries
    random.shuffle(candidates)
    return candidates


def pick_video(state):
    candidates = video_candidates(state)
    return candidates[0]


def pick_different_motion_video(state, excluded_video_id: str):
    candidates = [e for e in video_candidates(state) if e.get("id") != excluded_video_id]
    if not candidates:
        raise RuntimeError("Could not find a motion video different from the capture video")
    return candidates[0]


def pick_video_with_product(state):
    candidates = video_candidates(state)
    max_checks = int(os.environ.get("PRODUCT_PICK_MAX_CHECKS", "30"))
    checked = 0
    for entry in candidates:
        if checked >= max_checks:
            break
        video_id = entry.get("id")
        if not video_id:
            continue
        checked += 1
        tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{video_id}"
        product_url, product_title = extract_product_from_html(tiktok_url)
        if product_url or product_title:
            return entry, product_url, product_title
    raise RuntimeError(f"No affiliate/product TikTok video found after checking {checked} candidates")


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




def get_first_product_image(product_url: str):
    """Return first product image URL from TikTok Shop/Tokopedia PDP metadata."""
    if not product_url:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    }
    try:
        text = requests.get(product_url, headers=headers, timeout=45).text
    except Exception:
        return ""
    m = re.search(r'property="og:image"\s+content="([^"]+)"', text)
    if m:
        return urllib.parse.unquote(m.group(1).replace("&amp;", "&"))
    urls = re.findall(r'https?://[^"\'<>\\]+', text)
    for u in urls:
        u = urllib.parse.unquote(u.replace("&amp;", "&"))
        if any(host in u for host in ["p16-oec", "p19-oec", "ibyteimg"]) and any(ext in u for ext in ["webp", "jpeg", "jpg", "png"]):
            return u
    return ""


def download_product_image(product_url: str, job_dir: Path):
    image_url = get_first_product_image(product_url)
    if not image_url:
        raise RuntimeError(f"Could not extract first product image from product URL: {product_url}")
    headers = {"User-Agent": "Mozilla/5.0"}
    ext = ".webp"
    if ".png" in image_url:
        ext = ".png"
    elif ".jpg" in image_url or ".jpeg" in image_url:
        ext = ".jpg"
    out = job_dir / f"product_reference{ext}"
    with requests.get(image_url, headers=headers, stream=True, timeout=90) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_content(1024 * 128):
                if chunk:
                    f.write(chunk)
    if out.stat().st_size < 5 * 1024:
        raise RuntimeError("Downloaded product image is suspiciously small")
    return out, image_url


def capture_video_frames(video_path: Path, job_dir: Path, count: int = 5):
    """Capture candidate frames for outfit validation using OpenCV."""
    try:
        import cv2
    except Exception as e:
        raise RuntimeError(f"OpenCV is required for frame capture: {e}")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for frame capture: {video_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        raise RuntimeError("Video has no readable frames")
    # Avoid very first/last frames; sample across the clip.
    positions = [int(total * x / (count + 1)) for x in range(1, count + 1)]
    frames = []
    for idx, pos in enumerate(positions, start=1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, frame = cap.read()
        if not ok:
            continue
        out = job_dir / f"capture_{idx:02d}.jpg"
        cv2.imwrite(str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        frames.append(out)
    cap.release()
    if not frames:
        raise RuntimeError("No frames captured from video")
    return frames


def validate_capture_with_vision(frame_paths):
    """Pick a frame that clearly shows the product/outfit.

    Uses OpenAI vision when available. Falls back to the middle captured frame.
    """
    if OpenAI is None or not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY_FILE")):
        return frame_paths[len(frame_paths) // 2], "fallback_middle_frame"
    try:
        import base64
        client = OpenAI()
        content = [{"type": "input_text", "text": (
            "Choose the best frame for fashion try-on reference. It must clearly show the outfit/product/dress/top, "
            "prefer full or half body, minimal blur, minimal occlusion, and visible clothing details. "
            "Reply JSON only: {\"best_index\": 1-based-number, \"valid\": true/false, \"reason\": \"...\", \"outfit_description\": \"...\"}."
        )}]
        for p in frame_paths:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})
        resp = client.responses.create(model="gpt-5.5", input=[{"role": "user", "content": content}])
        text = resp.output_text.strip()
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0) if m else text)
        idx = max(1, min(len(frame_paths), int(data.get("best_index", 1)))) - 1
        if data.get("valid") is False:
            raise RuntimeError(f"No clear outfit frame: {data.get('reason', '')}")
        return frame_paths[idx], data.get("outfit_description") or data.get("reason") or "vision_validated"
    except Exception as e:
        # Do not hard-fail the entire pipeline for validation plumbing; pick middle frame.
        return frame_paths[len(frame_paths) // 2], f"validation_fallback: {e}"


def generate_reference_image(master_path: Path, outfit_capture_path: Path, job_dir: Path):
    """Generate master model wearing outfit from captured TikTok frame."""
    if OpenAI is None:
        raise RuntimeError("openai package is required for image generation")
    client = OpenAI()
    out_path = job_dir / "generated_reference.png"
    prompt = (
        "Edit the first image (master model). Preserve only the master's face/identity, pose, lighting, and carved wooden door background. "
        "Replace the full styling to match the product/reference image: top, bottom, hijab color, and accessories. "
        "Do not keep the cream hijab by default; change hijab color if it better matches the product styling. "
        "Remove the original bag by default unless a similar accessory is clearly part of the product styling. "
        "If the product/reference image shows a bottom, use that bottom; otherwise create a matching modest bottom. "
        "Match color, pattern, silhouette, sleeves, fabric texture, seams, buttons, collar, and visible clothing details. "
        "Do not copy marketplace UI, text, watermark, background, mannequin/body/face/hands from the product/reference image. "
        "Make it look like a natural realistic fashion photo."
    )
    with master_path.open("rb") as img1, outfit_capture_path.open("rb") as img2:
        # OpenAI Python SDK image edit/generation surface differs by version; use REST via requests for robustness.
        pass
    # Fallback to OpenClaw image tool is not available inside this standalone script.
    # Use OpenAI Images API through raw multipart endpoint.
    api_key = require_env("OPENAI_API_KEY")
    files = [
        ("image[]", (master_path.name, master_path.open("rb"), "image/png")),
        ("image[]", (outfit_capture_path.name, outfit_capture_path.open("rb"), "image/jpeg")),
    ]
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": "gpt-image-2", "prompt": prompt, "quality": "high", "size": "1024x1536"},
            files=files,
            timeout=240,
        )
    finally:
        for _, file_tuple in files:
            file_tuple[1].close()
    if not r.ok:
        raise RuntimeError(f"OpenAI image edit failed {r.status_code}: {r.text[:1000]}")
    data = r.json()
    b64 = data["data"][0].get("b64_json")
    if not b64:
        url = data["data"][0].get("url")
        if not url:
            raise RuntimeError(f"No image output from OpenAI: {data}")
        download_url(url, out_path)
    else:
        import base64
        out_path.write_bytes(base64.b64decode(b64))
    return out_path

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
    max_retries = int(os.environ.get("MAGNIFIC_MAX_RETRIES", "5"))
    retry_delay = int(os.environ.get("MAGNIFIC_RETRY_DELAY_SECONDS", "30"))
    last_error = None

    for attempt in range(1, max_retries + 1):
        r = requests.post(url, json=payload, headers={"x-magnific-api-key": key, "Accept": "application/json"}, timeout=120)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        if r.ok:
            return data

        message = str(data.get("message") or data.get("error") or data.get("raw") or data)
        retryable = (
            r.status_code in {403, 429, 500, 502, 503, 504}
            and any(term in message.lower() for term in ["blocked", "suspicious", "rate", "quota", "temporar", "try again"])
        )
        last_error = RuntimeError(f"Magnific function failed {r.status_code}: {data}")
        if retryable and attempt < max_retries:
            print(json.dumps({
                "phase": "magnific_retry",
                "attempt": attempt,
                "max_retries": max_retries,
                "status_code": r.status_code,
                "message": message,
                "sleep_seconds": retry_delay,
            }, ensure_ascii=False), flush=True)
            time.sleep(retry_delay)
            continue
        raise last_error

    raise last_error or RuntimeError("Magnific function failed without response")

def max_magnific_wait_seconds():
    return int(os.environ.get("MAGNIFIC_MAX_WAIT_SECONDS", str(12 * 60)))


def check_magnific_timeout(started_at: float, task_id: str):
    max_wait = max_magnific_wait_seconds()
    elapsed = time.time() - started_at
    if elapsed > max_wait:
        raise TimeoutError(f"Magnific task {task_id} did not complete within {max_wait} seconds")


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


def create_job_context(image_path: str | None = None):
    src_image = Path(image_path or require_env("MASTER_IMAGE_PATH")).expanduser().resolve()
    if not src_image.exists():
        raise RuntimeError(f"Image not found: {src_image}")

    created = now_utc()
    job_id = created.strftime("%Y%m%d%H%M%S") + "-" + os.urandom(3).hex()
    delete_after = iso(created + dt.timedelta(days=int(os.environ.get("RETENTION_DAYS", "7"))))
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    row = {"created_at": iso(created), "job_id": job_id, "status": "STARTED", "delete_after": delete_after}
    return src_image, job_id, delete_after, job_dir, row


def prepare(image_path: str | None = None):
    """Prepare a job up to the OpenClaw image-generation handoff.

    This intentionally does not generate the try-on reference image. The caller
    should use OpenClaw's image_generate tool with master_path + capture_path,
    then call `complete <job_id> <generated_reference_path>`.
    """
    load_env()
    DATA_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    cleanup_old()

    src_image, job_id, delete_after, job_dir, row = create_job_context(image_path)
    state = state_load()
    try:
        capture_entry, picked_product_url, picked_product_title = pick_video_with_product(state)
        capture_video_id = capture_entry["id"]
        capture_tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{capture_video_id}"
        row["product_video_url"] = capture_tiktok_url
        row["product_url"] = picked_product_url
        row["product_title"] = picked_product_title
        if not row["product_url"]:
            raise RuntimeError("Selected TikTok product video has no extractable affiliate/product URL")

        motion_entry = pick_different_motion_video(state, capture_video_id)
        motion_video_id = motion_entry["id"]
        motion_tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{motion_video_id}"
        row["motion_tiktok_video_url"] = motion_tiktok_url

        motion_local_video, motion_tikwm_data, _, _ = download_tiktok_video(motion_video_id, motion_tiktok_url, job_dir)

        motion_video_obj = f"magnific/automation/{job_id}/motion_source_{motion_video_id}.mp4"
        row["motion_supabase_video_url"] = supabase_upload(motion_local_video, motion_video_obj)

        product_image_path, product_image_source_url = download_product_image(row["product_url"], job_dir)
        product_image_obj = f"magnific/automation/{job_id}/product_reference{product_image_path.suffix.lower()}"
        row["product_image_url"] = product_image_source_url
        supabase_product_image_url = supabase_upload(product_image_path, product_image_obj)
        validation_note = "product_image_from_pdp"
        best_frame = product_image_path
        row["status"] = "NEEDS_REFERENCE_IMAGE"

        state.setdefault("jobs", []).append({
            "job_id": job_id,
            "created_at": row["created_at"],
            "delete_after": delete_after,
            "supabase_prefix": f"magnific/automation/{job_id}/",
        })
        prepared = state.setdefault("prepared_jobs", {})
        prepared[job_id] = {
            "row": row,
            "capture_video_id": capture_video_id,
            "motion_video_id": motion_video_id,
            "video_id": motion_video_id,
            "job_dir": str(job_dir),
            "master_path": str(src_image),
            "capture_path": str(best_frame),
            "product_image_path": str(product_image_path),
            "supabase_product_image_url": supabase_product_image_url,
            "motion_local_video_path": str(motion_local_video),
            "validation_note": validation_note,
        }
        state_save(state)
        log_row(row)
        payload = {
            "job_id": job_id,
            "status": row["status"],
            "master_path": str(src_image),
            "capture_path": str(best_frame),
            "product_video_url": row["product_video_url"],
            "product_url": row["product_url"],
            "product_title": row["product_title"],
            "product_image_url": row["product_image_url"],
            "supabase_product_image_url": supabase_product_image_url,
            "motion_tiktok_video_url": row["motion_tiktok_video_url"],
            "motion_supabase_video_url": row["motion_supabase_video_url"],
            "validation_note": validation_note,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
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


def complete(job_id: str, generated_reference_path: str):
    """Complete a prepared job after OpenClaw generated the try-on image."""
    load_env()
    ref_path = Path(generated_reference_path).expanduser().resolve()
    if not ref_path.exists():
        raise RuntimeError(f"Generated reference image not found: {ref_path}")
    state = state_load()
    prepared = state.get("prepared_jobs", {})
    info = prepared.get(job_id)
    if not info:
        raise RuntimeError(f"Prepared job not found: {job_id}")
    row = info.get("row", {})
    job_dir = Path(info["job_dir"])
    motion_video_id = info.get("motion_video_id") or info.get("video_id")
    capture_video_id = info.get("capture_video_id")
    try:
        gen_ref_obj = f"magnific/automation/{job_id}/generated_reference{ref_path.suffix.lower() or '.png'}"
        row["input_image_url"] = supabase_upload(ref_path, gen_ref_obj)

        gen = magnific_post({
            "action": "generate",
            "image_url": row["input_image_url"],
            "video_url": row["motion_supabase_video_url"],
            "character_orientation": "video",
            "cfg_scale": 0.5,
            "prompt": DEFAULT_PROMPT,
        })
        task_id = (gen.get("data") or {}).get("task_id")
        row["magnific_task_id"] = task_id or ""
        if not task_id:
            raise RuntimeError(f"No task_id from Magnific: {gen}")
        row["status"] = "PROCESSING"
        log_row(row)

        magnific_started_at = time.time()
        while True:
            check_magnific_timeout(magnific_started_at, task_id)
            time.sleep(min(180, max(1, max_magnific_wait_seconds() - int(time.time() - magnific_started_at))))
            check_magnific_timeout(magnific_started_at, task_id)
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
        for used_id in [capture_video_id, motion_video_id]:
            if used_id:
                recent.append(used_id)
        state["recent_video_ids"] = recent[-100:]
        prepared.pop(job_id, None)
        state_save(state)
        log_row(row)
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return 0
    except TimeoutError as e:
        row["status"] = "TIMEOUT"
        row["error"] = str(e)
        prepared[job_id] = {**info, "row": row}
        state_save(state)
        try:
            log_row(row)
        except Exception as e2:
            print(f"sheet/log failed too: {e2}", file=sys.stderr)
        print(json.dumps(row, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as e:
        row["status"] = "FAILED"
        row["error"] = str(e)
        prepared[job_id] = {**info, "row": row}
        state_save(state)
        try:
            log_row(row)
        except Exception as e2:
            print(f"sheet/log failed too: {e2}", file=sys.stderr)
        print(json.dumps(row, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


def run(image_path: str | None = None):
    load_env()
    DATA_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    cleanup_old()

    src_image = Path(image_path or require_env("MASTER_IMAGE_PATH")).expanduser().resolve()
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
        entry, picked_product_url, picked_product_title = pick_video_with_product(state)
        video_id = entry["id"]
        tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{video_id}"
        row["source_tiktok_url"] = tiktok_url

        local_video, tikwm_data, product_url, product_title = download_tiktok_video(video_id, tiktok_url, job_dir)
        row["source_product_url"] = product_url or picked_product_url
        row["source_product_title"] = product_title or picked_product_title

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
        row["status"] = "PROCESSING"
        log_row(row)

        magnific_started_at = time.time()
        while True:
            check_magnific_timeout(magnific_started_at, task_id)
            time.sleep(min(180, max(1, max_magnific_wait_seconds() - int(time.time() - magnific_started_at))))
            check_magnific_timeout(magnific_started_at, task_id)
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
    except TimeoutError as e:
        row["status"] = "TIMEOUT"
        row["error"] = str(e)
        try:
            log_row(row)
        except Exception as e2:
            print(f"sheet/log failed too: {e2}", file=sys.stderr)
        print(json.dumps(row, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1
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
    runp.add_argument("image", nargs="?", help="Optional image path. Defaults to MASTER_IMAGE_PATH.")
    prep = sub.add_parser("prepare")
    prep.add_argument("image", nargs="?", help="Optional master image path. Defaults to MASTER_IMAGE_PATH.")
    comp = sub.add_parser("complete")
    comp.add_argument("job_id")
    comp.add_argument("generated_reference_path")
    cleanp = sub.add_parser("cleanup")
    cleanp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    if args.cmd == "run":
        raise SystemExit(run(args.image))
    if args.cmd == "prepare":
        raise SystemExit(prepare(args.image))
    if args.cmd == "complete":
        raise SystemExit(complete(args.job_id, args.generated_reference_path))
    if args.cmd == "cleanup":
        removed = cleanup_old(dry_run=args.dry_run)
        print(json.dumps(removed, indent=2))


if __name__ == "__main__":
    main()
