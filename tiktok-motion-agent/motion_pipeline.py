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
TIKTOK_LIST_CACHE_PATH = DATA_DIR / "tiktok_entries_cache.json"

MODEST_TRYON_PROMPT = (
    "Create a realistic modest Muslim-friendly fashion try-on image. The person must remain hijab-friendly and fully modest. "
    "Keep the hijab/head covering intact. The hijab or an added inner scarf/underscarf MUST cover the entire neck, collarbone, upper chest, and any skin between chin and shirt. "
    "ABSOLUTE REQUIREMENTS: zero visible neck skin, zero visible collarbone, zero visible upper chest, no open neckline, no V-neck/open collar gap, no cleavage, no exposed chest, no shorts, no bare legs, no exposed thighs, no bare shoulders, no bare upper arms, no exposed waist or back, no transparent/sheer clothing, no tight body-revealing fit. "
    "Do not alter the affiliated product itself: keep the product's original cut, sleeve length, neckline, silhouette, color, pattern, texture, and style as accurately as possible. "
    "If the product reference is sleeveless, short, low-cut, open-collar, V-neck, cropped, sheer, tight, or revealing, keep that product unchanged and make the full outfit modest by layering separate clothing under or around it: matching long-sleeve inner shirt, high-neck turtleneck/dickey inner layer, neck-covering underscarf, leggings/full-length pants, long skirt, and/or outer cardigan/blazer. "
    "For button-up shirts, close the collar area visually with a high-neck inner layer or scarf so no skin is visible below the chin. "
    "The product must look like the same affiliated item; modest coverage should come from added inner/outer layers, not by modifying the product design. "
    "Final outfit should look natural for Indonesian Muslim women / hijab OOTD: long sleeves, fully covered neck and chest, covered legs to ankles, loose/comfortable fit."
)

DEFAULT_PROMPT = (
    "Transfer the body movement and camera rhythm from the TikTok reference video to the person in the reference image. "
    "Preserve identity, pose style, and background as much as possible while enforcing modest Muslim-friendly styling. "
    "Keep hijab/head covering intact and keep the outfit fully modest throughout all frames. The hijab/underscarf must cover the entire neck, collarbone, and upper chest in every frame. "
    "ABSOLUTE REQUIREMENTS: zero visible neck skin, zero visible collarbone, zero visible upper chest, no open neckline, no V-neck/open collar gap, no cleavage, no exposed chest, no shorts, no bare legs, no exposed thighs, no bare shoulders, no bare upper arms, no exposed waist/back, no transparent/sheer clothing, no tight body-revealing fit. "
    "Do not alter the affiliated product itself: keep its original cut, sleeve length, neckline, silhouette, color, pattern, texture, and style as accurately as possible. "
    "If any source product is sleeveless, short, low-cut, open-collar, V-neck, cropped, sheer, tight, or revealing, keep the product unchanged and make the full outfit modest by layering separate clothing under or around it: matching long-sleeve inner shirt, high-neck turtleneck/dickey inner layer, neck-covering underscarf, leggings/full-length pants, long skirt, and/or outer cardigan/blazer. "
    "For button-up shirts, close the collar area visually with a high-neck inner layer or scarf so no skin is visible below the chin. "
    "The product must look like the same affiliated item; modest coverage should come from added inner/outer layers, not by modifying the product design. "
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
    "uploaded_at",
    # Provider metadata. Appended after the existing sheet schema so old columns
    # keep their positions.
    "video_provider",
    "provider_auth_label",
    "provider_job_id",
    "provider_status",
    "dreamface_animate_id",
    "dreamface_work_id",
]

STATUS_VALUES = [
    "STARTED",
    "NEEDS_REFERENCE_IMAGE",
    "QUEUED",
    "SUBMITTED",
    "PROCESSING",
    "COMPLETED",
    "READY_TO_UPLOAD",
    "UPLOADED",
    "AFFILIATED",
    "FAILED",
    "TIMEOUT",
]


TERMINAL_STATUSES = {"COMPLETED", "READY_TO_UPLOAD", "UPLOADED", "AFFILIATED", "FAILED", "TIMEOUT"}
ACTIVE_STATUSES = {"QUEUED", "SUBMITTED", "PROCESSING"}

STATUS_COLORS = {
    # Intentionally high-contrast and unique per status.
    "STARTED": {"backgroundColor": {"red": 0.80, "green": 0.86, "blue": 1.00}},          # blue
    "NEEDS_REFERENCE_IMAGE": {"backgroundColor": {"red": 1.00, "green": 0.93, "blue": 0.55}}, # yellow
    "QUEUED": {"backgroundColor": {"red": 0.86, "green": 0.80, "blue": 1.00}},          # lavender
    "SUBMITTED": {"backgroundColor": {"red": 0.74, "green": 0.82, "blue": 1.00}},       # periwinkle
    "PROCESSING": {"backgroundColor": {"red": 0.65, "green": 0.92, "blue": 1.00}},       # cyan
    "COMPLETED": {"backgroundColor": {"red": 0.70, "green": 0.92, "blue": 0.70}},        # green
    "READY_TO_UPLOAD": {"backgroundColor": {"red": 0.58, "green": 1.00, "blue": 0.78}},  # mint
    "UPLOADED": {"backgroundColor": {"red": 0.55, "green": 0.78, "blue": 1.00}},         # stronger blue
    "AFFILIATED": {"backgroundColor": {"red": 0.84, "green": 0.67, "blue": 1.00}},       # purple
    "FAILED": {"backgroundColor": {"red": 1.00, "green": 0.62, "blue": 0.62}},           # red
    "TIMEOUT": {"backgroundColor": {"red": 1.00, "green": 0.73, "blue": 0.45}},          # orange
}


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
    sync_sheet_table_columns(ws)
    ensure_sheet_status_controls(ws)


def sync_sheet_table_columns(ws):
    """Keep Google Sheets Table metadata aligned with COLUMNS.

    Google Sheets typed table columns (including dropdowns) are not updated by
    normal header/data-validation calls, so update the table definition too.
    """
    try:
        metadata = ws.spreadsheet.fetch_sheet_metadata()
        table = None
        for sheet in metadata.get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") == ws.id:
                tables = sheet.get("tables") or []
                table = tables[0] if tables else None
                break
        if not table:
            return

        table_range = dict(table.get("range") or {})
        table_range["sheetId"] = ws.id
        table_range.setdefault("startRowIndex", 0)
        table_range.setdefault("startColumnIndex", 0)
        table_range["endColumnIndex"] = max(table_range.get("endColumnIndex", 0), len(COLUMNS))
        table_range["endRowIndex"] = max(table_range.get("endRowIndex", 0), max(ws.row_count, 1000))

        old_by_name = {c.get("columnName"): c for c in table.get("columnProperties", [])}
        column_properties = []
        for index, name in enumerate(COLUMNS):
            prop = dict(old_by_name.get(name) or {})
            prop["columnIndex"] = index
            prop["columnName"] = name
            if name == "status":
                prop["columnType"] = "DROPDOWN"
                prop["dataValidationRule"] = {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": s} for s in STATUS_VALUES],
                    }
                }
            column_properties.append(prop)

        ws.spreadsheet.batch_update({
            "requests": [{
                "updateTable": {
                    "table": {
                        "tableId": table["tableId"],
                        "range": table_range,
                        "columnProperties": column_properties,
                    },
                    "fields": "range,columnProperties",
                }
            }]
        })
    except Exception as e:
        print(f"table metadata sync skipped: {e}", file=sys.stderr)


def ensure_sheet_status_controls(ws):
    """Add status dropdown enum and color marks to the Google Sheet.

    The local CSV remains plain CSV; the enum/color UX is applied in Google
    Sheets whenever gspread is available.
    """
    from gspread.utils import ValidationConditionType

    status_col = chr(ord("A") + COLUMNS.index("status"))
    status_range = f"{status_col}2:{status_col}1000"
    try:
        ws.add_validation(
            status_range,
            ValidationConditionType.one_of_list,
            STATUS_VALUES,
            inputMessage="Choose a pipeline status",
            strict=True,
            showCustomUi=True,
        )
    except Exception as e:
        # Some Google Sheets/table typed columns reject classic data validation.
        # Keep formatting working and leave existing dropdowns/chips intact.
        print(f"status validation skipped: {e}", file=sys.stderr)
    apply_status_conditional_colors(ws)
    apply_status_colors(ws)


def status_text_format(status: str):
    return {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": True}


def apply_status_conditional_colors(ws):
    """Install conditional formatting so status colors stay distinct.

    Direct cell formats can be hard to notice when Google Sheets renders
    dropdown chips. Conditional rules are more durable for future edits.
    """
    sheet_id = ws.id
    status_idx = COLUMNS.index("status")
    requests = []
    # Add rules in reverse at index 0 so final priority follows STATUS_VALUES.
    for status in reversed(STATUS_VALUES):
        fmt = {
            **STATUS_COLORS[status],
            "textFormat": status_text_format(status),
        }
        requests.append({
            "addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": status_idx,
                        "endColumnIndex": status_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": status}],
                        },
                        "format": fmt,
                    },
                },
            }
        })
    ws.spreadsheet.batch_update({"requests": requests})


def apply_status_colors(ws):
    status_col = chr(ord("A") + COLUMNS.index("status"))
    values = ws.col_values(COLUMNS.index("status") + 1)
    formats = []
    for row_index, status in enumerate(values[1:], start=2):
        if not status:
            continue
        fmt = STATUS_COLORS.get(status)
        if fmt:
            formats.append({"range": f"{status_col}{row_index}", "format": fmt})
    if formats:
        ws.batch_format(formats)



def active_generation_from_state(state: dict, exclude_job_id: str | None = None) -> dict | None:
    """Return an active unfinished generation, if any.

    The local state file is the source of truth for in-flight video-provider work.
    Waiting-for-reference jobs are intentionally not considered running; only
    queued/submitted/processing provider jobs block a new generation.
    """
    for job_id, info in (state.get("prepared_jobs") or {}).items():
        if exclude_job_id and job_id == exclude_job_id:
            continue
        row = info.get("row") or {}
        status = row.get("status") or ""
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id,
                "status": status,
                "created_at": row.get("created_at", ""),
                "provider": row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            }

    # Fallback for older state shapes / interrupted writes.
    for job in state.get("jobs") or []:
        job_id = job.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        status = job.get("status")
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id or "",
                "status": status,
                "created_at": job.get("created_at", ""),
                "provider": job.get("video_provider", ""),
            }
    return None


def active_generation_from_csv(exclude_job_id: str | None = None) -> dict | None:
    if not RUNS_CSV.exists():
        return None
    try:
        with RUNS_CSV.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return None
    for row in reversed(rows):
        job_id = row.get("job_id")
        if exclude_job_id and job_id == exclude_job_id:
            continue
        status = row.get("status") or ""
        if status in ACTIVE_STATUSES:
            return {
                "job_id": job_id or "",
                "status": status,
                "created_at": row.get("created_at", ""),
                "provider": row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
            }
    return None


def find_active_generation(exclude_job_id: str | None = None) -> dict | None:
    state_active = active_generation_from_state(state_load(), exclude_job_id=exclude_job_id)
    if state_active:
        return state_active
    return active_generation_from_csv(exclude_job_id=exclude_job_id)


def assert_no_active_generation(exclude_job_id: str | None = None):
    active = find_active_generation(exclude_job_id=exclude_job_id)
    if not active:
        return
    raise RuntimeError(
        json.dumps(
            {
                "ok": False,
                "code": "GENERATION_ALREADY_RUNNING",
                "message": "A video generation is already running. Please wait until it finishes.",
                "active_job_id": active.get("job_id", ""),
                "active_status": active.get("status", ""),
                "active_provider": active.get("provider", ""),
                "active_created_at": active.get("created_at", ""),
            },
            ensure_ascii=False,
        )
    )

def validate_status(row: dict):
    status = row.get("status")
    if status and status not in STATUS_VALUES:
        print(f"warning: unknown status {status!r}; allowed={STATUS_VALUES}", file=sys.stderr)


def normalize_row(row: dict):
    if row.get("status") == "UPLOADED" and not row.get("uploaded_at"):
        row = dict(row)
        row["uploaded_at"] = iso(now_utc())
    return row


def append_sheet(row: dict):
    row = normalize_row(row)
    validate_status(row)
    ws = get_sheet()
    ensure_sheet_header(ws)
    ws.append_row([row.get(c, "") for c in COLUMNS], value_input_option="RAW")


def append_local_csv(row: dict):
    row = normalize_row(row)
    validate_status(row)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in COLUMNS})


def upsert_local_csv(row: dict):
    row = normalize_row(row)
    validate_status(row)
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
    row = normalize_row(row)
    validate_status(row)
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
        row_index = len(ws.col_values(1))
    status = row.get("status")
    fmt = STATUS_COLORS.get(status)
    if fmt and row_index:
        status_col = chr(ord("A") + COLUMNS.index("status"))
        ws.format(f"{status_col}{row_index}", fmt)


def log_row(row: dict):
    upsert_local_csv(row)
    try:
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet log skipped: {e}", file=sys.stderr)


def yt_dlp_entries(limit=150):
    cache_seconds = int(os.environ.get("TIKTOK_LIST_CACHE_SECONDS", "21600"))
    if TIKTOK_LIST_CACHE_PATH.exists():
        try:
            cached = json.loads(TIKTOK_LIST_CACHE_PATH.read_text())
            age = time.time() - float(cached.get("fetched_at", 0))
            entries = cached.get("entries") or []
            if age < cache_seconds and entries:
                return entries[:limit]
        except Exception:
            pass

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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TIKTOK_LIST_CACHE_PATH.write_text(json.dumps({"fetched_at": time.time(), "entries": entries}, ensure_ascii=False) + "\n")
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
    product_cache = state.setdefault("product_cache", {})
    max_checks = int(os.environ.get("PRODUCT_PICK_MAX_CHECKS", "30"))
    checked = 0
    dirty = False
    for entry in candidates:
        if checked >= max_checks:
            break
        video_id = entry.get("id")
        if not video_id:
            continue
        checked += 1
        cached = product_cache.get(video_id)
        if cached and cached.get("product_image_url"):
            product_url = cached.get("product_url", "")
            product_title = cached.get("product_title", "")
            product_image_url = cached.get("product_image_url", "")
        else:
            # Re-parse older cache entries too: Tokopedia PDPs often show a captcha,
            # but the TikTok video HTML usually embeds the product card image.
            tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{video_id}"
            product_url, product_title, product_image_url = extract_product_from_html(tiktok_url)
            product_cache[video_id] = {
                "product_url": product_url,
                "product_title": product_title,
                "product_image_url": product_image_url,
                "checked_at": iso(now_utc()),
            }
            dirty = True
        if product_url or product_title:
            if dirty:
                state_save(state)
            return entry, product_url, product_title, product_image_url
    if dirty:
        state_save(state)
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
        return "", "", ""
    # Product info often appears as escaped JSON inside the HTML.
    idx = text.find("product_id")
    if idx == -1:
        return "", "", ""
    window = text[max(0, idx - 10000): idx + 30000]
    # Repeated unescape helps nested JSON strings.
    for _ in range(3):
        window = window.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        window = urllib.parse.unquote(window)
    title = ""
    product_id = ""
    seo_url = ""
    image_url = first_oec_image_from_text(window)
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
    return seo_url, title, image_url


def oec_uri_to_image_url(uri: str):
    uri = uri.strip().strip('"').replace("\\/", "/")
    if not uri.startswith("tos-"):
        return ""
    return (
        f"https://p16-oec-sg.ibyteimg.com/{uri}"
        "~tplv-aphluv4xwc-resize-jpeg:800:800.jpeg"
        "?dr=15584&t=555f072d&ps=933b5bde&shp=615b9c44&shcp=57d7afb6&idc=my&from=1633393732"
    )


def first_oec_image_from_text(text: str):
    # Prefer the product's ordered image list. TikTok's product card often has
    # full `cover_url`/`img_url` fields later in the JSON; those can correspond
    # to a card cover rather than the PDP's first gallery image.
    for m in re.finditer(r'"img"\s*:\s*\[(.*?)\]', text, re.S):
        for uri in re.findall(r'"(tos-[^"]+)"', m.group(1)):
            image_url = oec_uri_to_image_url(uri)
            if image_url:
                return image_url

    urls = re.findall(r'https?://[^"\'<>\\\s]+', text)
    for u in urls:
        u = urllib.parse.unquote(u.replace("&amp;", "&").replace("\\/", "/"))
        if any(host in u for host in ["p16-oec", "p19-oec", "ibyteimg"]):
            if any(ext in u for ext in ["webp", "jpeg", "jpg", "png"]):
                return u
    return ""




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
    return first_oec_image_from_text(text)


def download_product_image(product_url: str, job_dir: Path, fallback_image_url: str = ""):
    image_url = fallback_image_url or get_first_product_image(product_url)
    if not image_url:
        raise RuntimeError(f"Could not extract first product image from product URL or TikTok product card: {product_url}")
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


def download_tiktok_video(video_id: str, tiktok_url: str, job_dir: Path):
    """Download a TikTok video through tikwm and return local path + metadata.

    Product URL/title are parsed separately from TikTok HTML, because tikwm does
    not consistently expose affiliate anchors.
    """
    data = get_tikwm_data(tiktok_url)
    video_url = data.get("hdplay") or data.get("play") or data.get("wmplay")
    if not video_url:
        raise RuntimeError(f"No downloadable video URL from tikwm for {tiktok_url}")
    if video_url.startswith("//"):
        video_url = "https:" + video_url
    out = job_dir / f"tiktok_{video_id}.mp4"
    download_url(video_url, out)
    if out.stat().st_size < 100 * 1024:
        raise RuntimeError("Downloaded TikTok video is suspiciously small")
    product_url, product_title, _ = extract_product_from_html(tiktok_url)
    return out, data, product_url, product_title


def download_url(url: str, path: Path):
    with requests.get(url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024 * 256):
                if chunk:
                    f.write(chunk)


def public_storage_url(object_path: str) -> str:
    project_ref = require_env("SUPABASE_PROJECT_REF")
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    return f"https://{project_ref}.supabase.co/storage/v1/object/public/{urllib.parse.quote(bucket, safe='')}/{urllib.parse.quote(object_path, safe='/')}"


def supabase_upload(local_path: Path, object_path: str) -> str:
    """Upload a file to the configured public Supabase bucket and return public URL."""
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    local_path = Path(local_path)
    if not local_path.exists():
        raise RuntimeError(f"Upload source not found: {local_path}")
    target = f"ss:///{bucket}/{object_path.strip('/')}"
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    public_url = public_storage_url(object_path.strip('/'))
    content_type = mimetypes.guess_type(str(local_path))[0]
    if local_path.suffix.lower() == ".mp4":
        content_type = "video/mp4"
    cmd = [
        "npx", "supabase", "--experimental", "storage", "cp", str(local_path), target, "--linked",
    ]
    if content_type:
        cmd.extend(["--content-type", content_type])
    try:
        subprocess.run(cmd, cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        output = e.stdout or ""
        # Supabase CLI sometimes uploads the object, then exits 1 with a
        # Duplicate/409 on retry. Treat it as success only if the public object
        # is actually reachable.
        if "Duplicate" in output or '"statusCode":"409"' in output or "already exists" in output:
            try:
                r = requests.head(public_url, timeout=30)
                if 200 <= r.status_code < 300:
                    return public_url
            except Exception:
                pass
        raise RuntimeError(f"Supabase upload failed for {target}: {output.strip() or e}") from e
    return public_url


def magnific_function_url() -> str:
    return f"https://{require_env('SUPABASE_PROJECT_REF')}.supabase.co/functions/v1/magnific-motion"


def retryable_magnific_error(status_code: int, text: str) -> bool:
    lowered = text.lower()
    return status_code in {403, 429, 500, 502, 503, 504} or "blocked" in lowered or "rate" in lowered


def magnific_post(payload: dict) -> dict:
    api_key = require_env("MAGNIFIC_API_KEY")
    max_retries = int(os.environ.get("MAGNIFIC_MAX_RETRIES", "5"))
    retry_delay = int(os.environ.get("MAGNIFIC_RETRY_DELAY_SECONDS", "30"))
    last_detail = None
    for attempt in range(max_retries + 1):
        r = requests.post(
            magnific_function_url(),
            headers={"Content-Type": "application/json", "x-magnific-api-key": api_key},
            json=payload,
            timeout=120,
        )
        text = r.text
        try:
            data = r.json()
        except Exception:
            data = {"raw": text}
        if 200 <= r.status_code < 300:
            return data
        last_detail = f"Magnific function failed {r.status_code}: {data}"
        if attempt < max_retries and retryable_magnific_error(r.status_code, text):
            time.sleep(retry_delay)
            continue
        raise RuntimeError(last_detail)
    raise RuntimeError(last_detail or "Magnific function failed")


def max_magnific_wait_seconds() -> int:
    return int(os.environ.get("MAGNIFIC_MAX_WAIT_SECONDS", "3600"))


def check_magnific_timeout(started_at: float, task_id: str):
    if time.time() - started_at > max_magnific_wait_seconds():
        raise TimeoutError(f"Magnific task timed out: {task_id}")


def selected_video_provider(provider: str | None = None) -> str:
    value = (provider or os.environ.get("VIDEO_PROVIDER") or "magnific").strip().lower()
    aliases = {"magnefic": "magnific", "dream_face": "dreamface", "dream-face": "dreamface"}
    value = aliases.get(value, value)
    if value not in {"magnific", "dreamface"}:
        raise RuntimeError(f"Unsupported VIDEO_PROVIDER: {value!r}")
    return value


def dreamface_auths() -> list[dict]:
    """Load DreamFace auth pool from env.

    Preferred env shape:
      DREAMFACE_AUTHS_JSON='[{"label":"df1","token":"...","account_id":"...","user_id":"...","client_id":"...","cookie":"..."}]'

    Fallback single-auth envs are also supported:
      DREAMFACE_TOKEN, DREAMFACE_ACCOUNT_ID, DREAMFACE_USER_ID,
      DREAMFACE_CLIENT_ID, DREAMFACE_COOKIE, DREAMFACE_AUTH_LABEL
    """
    raw = os.environ.get("DREAMFACE_AUTHS_JSON", "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    auths: list[dict] = []
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("auths") or parsed.get("accounts") or []
        if not isinstance(parsed, list):
            raise RuntimeError("DREAMFACE_AUTHS_JSON must be a JSON list or object with auths/accounts")
        auths = [dict(a) for a in parsed]
    elif os.environ.get("DREAMFACE_TOKEN"):
        auths = [{
            "label": os.environ.get("DREAMFACE_AUTH_LABEL") or "dreamface-1",
            "token": os.environ.get("DREAMFACE_TOKEN"),
            "cookie": os.environ.get("DREAMFACE_COOKIE", ""),
            "client_id": os.environ.get("DREAMFACE_CLIENT_ID", "3fac5089bfb993119e743f657cb31b2b"),
            "account_id": os.environ.get("DREAMFACE_ACCOUNT_ID"),
            "user_id": os.environ.get("DREAMFACE_USER_ID"),
        }]

    cleaned = []
    for idx, auth in enumerate(auths, start=1):
        auth.setdefault("label", f"dreamface-{idx}")
        auth.setdefault("client_id", "3fac5089bfb993119e743f657cb31b2b")
        missing = [k for k in ["token", "account_id", "user_id"] if not auth.get(k)]
        if missing:
            raise RuntimeError(f"DreamFace auth {auth.get('label')!r} missing: {', '.join(missing)}")
        cleaned.append(auth)
    if not cleaned:
        raise RuntimeError("No DreamFace auth configured. Set DREAMFACE_AUTHS_JSON or DREAMFACE_TOKEN/ACCOUNT_ID/USER_ID.")
    return cleaned


def dreamface_headers(auth: dict, referer: str = "https://www.dreamfaceapp.com/creation", json_body: bool = True) -> dict:
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "client-id": str(auth.get("client_id") or "3fac5089bfb993119e743f657cb31b2b"),
        "dream-face-web": "dream-face-web",
        "origin": "https://www.dreamfaceapp.com",
        "referer": referer,
        "token": str(auth["token"]),
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    if json_body:
        headers["content-type"] = "application/json"
    if auth.get("cookie"):
        headers["Cookie"] = str(auth["cookie"])
    return headers


def dreamface_request(method: str, path: str, auth: dict, *, json_body: dict | None = None, params: dict | None = None, referer: str = "https://www.dreamfaceapp.com/creation") -> dict:
    url = f"https://www.dreamfaceapp.com{path}"
    r = requests.request(
        method,
        url,
        headers=dreamface_headers(auth, referer=referer, json_body=json_body is not None),
        json=json_body,
        params=params,
        timeout=120,
    )
    text = r.text
    try:
        data = r.json()
    except Exception:
        data = {"raw": text}
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"DreamFace {method} {path} failed {r.status_code}: {data}")
    if data.get("status_code") and data.get("status_code") != "THS12140000000":
        raise RuntimeError(f"DreamFace {method} {path} returned {data.get('status_code')}: {data.get('status_msg') or data}")
    return data


def dreamface_quota(auth: dict) -> dict:
    body = {"user_id": auth["user_id"], "account_id": auth["account_id"]}
    data = dreamface_request("POST", "/dw-server/rights/get_free_rights", auth, json_body=body)
    return data.get("data") or {}


def select_dreamface_auth() -> tuple[dict, dict]:
    exhausted = []
    for auth in dreamface_auths():
        quota = dreamface_quota(auth)
        remain = int(quota.get("remain_count") or 0)
        if remain > 0:
            return auth, quota
        exhausted.append({"label": auth.get("label"), "quota": quota})
    raise RuntimeError(json.dumps({
        "ok": False,
        "code": "DREAMFACE_QUOTA_EXHAUSTED",
        "message": "All configured DreamFace accounts have no quota left.",
        "auths": exhausted,
    }, ensure_ascii=False))


def dreamface_submit(auth: dict, image_url: str, video_url: str) -> str:
    body = {
        "media": {
            "images": [{"url": image_url}],
            "videos": [{"url": video_url}],
            "texts": [],
            "audios": [],
        },
        "user": {
            "account_id": auth["account_id"],
            "app_version": os.environ.get("DREAMFACE_APP_VERSION", "4.7.1"),
            "platform_type": "WEB",
            "user_id": auth["user_id"],
        },
        "template": {
            "template_id": os.environ.get("DREAMFACE_TEMPLATE_ID", "REPLACE_DANCE"),
            "play_types": [os.environ.get("DREAMFACE_TEMPLATE_ID", "REPLACE_DANCE")],
            "project_id": "",
        },
        "output": {
            "width": int(os.environ.get("DREAMFACE_OUTPUT_WIDTH", "1080")),
            "height": int(os.environ.get("DREAMFACE_OUTPUT_HEIGHT", "1080")),
            "ratio": os.environ.get("DREAMFACE_OUTPUT_RATIO", "1:1"),
            "duration": int(os.environ.get("DREAMFACE_DURATION", "5")),
            "resolution": os.environ.get("DREAMFACE_RESOLUTION", "480P"),
            "vertical": os.environ.get("DREAMFACE_VERTICAL", "true").lower() != "false",
            "replace_background": os.environ.get("DREAMFACE_REPLACE_BACKGROUND", "false").lower() == "true",
        },
        "ext_info": {
            "sing_title": os.environ.get("DREAMFACE_WORK_NAME", "ACT ANIMATE"),
            "is_sound_effect": os.environ.get("DREAMFACE_SOUND_EFFECT", "true").lower() != "false",
            "animate_channel": "",
            "route_url": "",
            "timbre_id": "",
            "cover": "",
            "video_id": "",
            "genders": [],
        },
        "work_type": os.environ.get("DREAMFACE_WORK_TYPE", "ACT_ANIMATE"),
        "create_work_session": False,
        "asset_info": {"asset_id": "", "original_video_url": "", "file_name": ""},
    }
    data = dreamface_request("POST", "/dw-server/task/v2/submit", auth, json_body=body, referer="https://www.dreamfaceapp.com/apps/dreamact")
    animate_id = (data.get("data") or {}).get("animate_image_id")
    if not animate_id:
        raise RuntimeError(f"No animate_image_id from DreamFace submit: {data}")
    return animate_id


def dreamface_recent_creation(auth: dict, animate_id: str, size: int | None = None) -> dict | None:
    # Size 1 matches the normal single-worker account flow. If multiple systems
    # share one DreamFace account, bump DREAMFACE_RECENT_SIZE and match animate_id.
    size = size or int(os.environ.get("DREAMFACE_RECENT_SIZE", "1"))
    body = {
        "user_id": auth["user_id"],
        "account_id": auth["account_id"],
        "page": 1,
        "size": size,
        "is_web": True,
        "app_version": os.environ.get("DREAMFACE_APP_VERSION", "4.7.1"),
    }
    data = dreamface_request("POST", "/dw-server/work/v2/get_recent_creation_list", auth, json_body=body)
    items = ((data.get("data") or {}).get("list") or [])
    for item in items:
        if item.get("animate_id") == animate_id:
            return item
    return items[0] if size == 1 and items else None


def dreamface_work_detail(auth: dict, work_id: str) -> dict:
    data = dreamface_request("GET", "/dw-server/work/get_work_detail_web", auth, params={"work_id": work_id}, json_body=None)
    return data.get("data") or {}


def max_dreamface_wait_seconds() -> int:
    return int(os.environ.get("DREAMFACE_MAX_WAIT_SECONDS", "1800"))


def dreamface_poll_interval_seconds() -> int:
    return int(os.environ.get("DREAMFACE_POLL_INTERVAL_SECONDS", "45"))


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
    should use OpenClaw's image_generate tool with master_path + product_image_path,
    then call `complete <job_id> <generated_reference_path>`.
    """
    load_env()
    DATA_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    cleanup_old()
    assert_no_active_generation()

    src_image, job_id, delete_after, job_dir, row = create_job_context(image_path)
    state = state_load()
    try:
        product_entry, picked_product_url, picked_product_title, picked_product_image_url = pick_video_with_product(state)
        product_video_id = product_entry["id"]
        product_tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{product_video_id}"
        row["product_video_url"] = product_tiktok_url
        row["product_url"] = picked_product_url
        row["product_title"] = picked_product_title
        if not row["product_url"]:
            raise RuntimeError("Selected TikTok product video has no extractable affiliate/product URL")

        motion_entry = pick_different_motion_video(state, product_video_id)
        motion_video_id = motion_entry["id"]
        motion_tiktok_url = f"https://www.tiktok.com/@keranjang_tiktok08/video/{motion_video_id}"
        row["motion_tiktok_video_url"] = motion_tiktok_url

        motion_local_video, motion_tikwm_data, _, _ = download_tiktok_video(motion_video_id, motion_tiktok_url, job_dir)

        motion_video_obj = f"magnific/automation/{job_id}/motion_source_{motion_video_id}.mp4"
        row["motion_supabase_video_url"] = supabase_upload(motion_local_video, motion_video_obj)

        product_image_path, product_image_source_url = download_product_image(row["product_url"], job_dir, picked_product_image_url)
        product_image_obj = f"magnific/automation/{job_id}/product_reference{product_image_path.suffix.lower()}"
        row["product_image_url"] = product_image_source_url
        supabase_product_image_url = supabase_upload(product_image_path, product_image_obj)
        validation_note = "product_image_from_pdp"
        row["status"] = "NEEDS_REFERENCE_IMAGE"

        state.setdefault("jobs", []).append({
            "job_id": job_id,
            "created_at": row["created_at"],
            "delete_after": delete_after,
            "supabase_prefix": f"magnific/automation/{job_id}/",
        })
        prepared = state.setdefault("prepared_jobs", {})
        prompt_path = job_dir / "modest_tryon_prompt.txt"
        prompt_path.write_text(MODEST_TRYON_PROMPT + "\n", encoding="utf-8")

        prepared[job_id] = {
            "row": row,
            "product_video_id": product_video_id,
            "motion_video_id": motion_video_id,
            "video_id": motion_video_id,
            "job_dir": str(job_dir),
            "master_path": str(src_image),
            "product_image_path": str(product_image_path),
            "supabase_product_image_url": supabase_product_image_url,
            "motion_local_video_path": str(motion_local_video),
            "modest_tryon_prompt": MODEST_TRYON_PROMPT,
            "modest_tryon_prompt_path": str(prompt_path),
            "validation_note": validation_note,
        }
        state_save(state)
        log_row(row)
        # Keep stdout intentionally tiny; details are already in state + Sheet.
        payload = {
            "job_id": job_id,
            "master_path": str(src_image),
            "product_image_path": str(product_image_path),
            "prompt_path": str(prompt_path),
            "prompt": MODEST_TRYON_PROMPT,
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


def complete(job_id: str, generated_reference_path: str, provider: str | None = None):
    """Complete a prepared job after OpenClaw generated the try-on image."""
    load_env()
    ref_path = Path(generated_reference_path).expanduser().resolve()
    if not ref_path.exists():
        raise RuntimeError(f"Generated reference image not found: {ref_path}")
    state = state_load()
    assert_no_active_generation(exclude_job_id=job_id)
    prepared = state.get("prepared_jobs", {})
    info = prepared.get(job_id)
    if not info:
        raise RuntimeError(f"Prepared job not found: {job_id}")
    row = info.get("row", {})
    job_dir = Path(info["job_dir"])
    motion_video_id = info.get("motion_video_id") or info.get("video_id")
    product_video_id = info.get("product_video_id") or info.get("capture_video_id")
    provider_name = selected_video_provider(provider)
    try:
        row["status"] = "SUBMITTED"
        row["error"] = ""
        row["video_provider"] = provider_name
        prepared[job_id] = {**info, "row": row}
        state_save(state)
        log_row(row)

        gen_ref_obj = f"magnific/automation/{job_id}/generated_reference{ref_path.suffix.lower() or '.png'}"
        row["input_image_url"] = row.get("input_image_url") or supabase_upload(ref_path, gen_ref_obj)

        if provider_name == "magnific":
            task_id = row.get("magnific_task_id")
            if not task_id:
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
                row["provider_job_id"] = task_id or ""
                if not task_id:
                    raise RuntimeError(f"No task_id from Magnific: {gen}")
            row["status"] = "PROCESSING"
            row["provider_status"] = "PROCESSING"
            row["error"] = ""
            prepared[job_id] = {**info, "row": row}
            state_save(state)
            log_row(row)

            magnific_started_at = time.time()
            while True:
                check_magnific_timeout(magnific_started_at, task_id)
                time.sleep(min(120, max(1, max_magnific_wait_seconds() - int(time.time() - magnific_started_at))))
                check_magnific_timeout(magnific_started_at, task_id)
                status = magnific_post({"action": "status", "task_id": task_id})
                d = status.get("data") or status
                state_value = d.get("status")
                row["provider_status"] = state_value or ""
                prepared[job_id] = {**info, "row": row}
                state_save(state)
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
                    row["provider_status"] = "COMPLETED"
                    break
                if state_value in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}:
                    raise RuntimeError(f"Magnific ended with {state_value}: {status}")
        else:
            auth_label = row.get("provider_auth_label")
            selected_auth = None
            quota = None
            if auth_label:
                for auth in dreamface_auths():
                    if auth.get("label") == auth_label:
                        selected_auth = auth
                        break
                if not selected_auth:
                    raise RuntimeError(f"Configured DreamFace auth not found for label: {auth_label}")
            else:
                selected_auth, quota = select_dreamface_auth()
                row["provider_auth_label"] = selected_auth.get("label", "")
            if quota:
                row["provider_status"] = f"quota {quota.get('remain_count')}/{quota.get('total_count')}"

            animate_id = row.get("dreamface_animate_id")
            if not animate_id:
                animate_id = dreamface_submit(selected_auth, row["input_image_url"], row["motion_supabase_video_url"])
                row["dreamface_animate_id"] = animate_id
                row["provider_job_id"] = animate_id
            row["status"] = "PROCESSING"
            row["provider_status"] = "PROCESSING"
            row["error"] = ""
            prepared[job_id] = {**info, "row": row}
            state_save(state)
            log_row(row)

            started_at = time.time()
            work_id = row.get("dreamface_work_id")
            while True:
                if time.time() - started_at > max_dreamface_wait_seconds():
                    raise TimeoutError(f"DreamFace task timed out: {animate_id}")
                time.sleep(min(dreamface_poll_interval_seconds(), max(1, max_dreamface_wait_seconds() - int(time.time() - started_at))))
                item = None if work_id else dreamface_recent_creation(selected_auth, animate_id)
                if item:
                    row["provider_status"] = str(item.get("web_work_status", ""))
                    if item.get("animate_id") and item.get("animate_id") != animate_id and os.environ.get("DREAMFACE_RECENT_SIZE", "1") != "1":
                        prepared[job_id] = {**info, "row": row}
                        state_save(state)
                        log_row(row)
                        continue
                    work_id = item.get("id") or work_id
                    row["dreamface_work_id"] = work_id or ""
                    prepared[job_id] = {**info, "row": row}
                    state_save(state)
                    log_row(row)
                    if item.get("web_work_status") not in {200, "200"}:
                        continue

                if not work_id:
                    prepared[job_id] = {**info, "row": row}
                    state_save(state)
                    continue

                detail = dreamface_work_detail(selected_auth, work_id)
                work_url = detail.get("nw_work_url") or detail.get("work_url")
                if not work_url:
                    prepared[job_id] = {**info, "row": row}
                    state_save(state)
                    continue
                result_path = job_dir / f"result_dreamface_{work_id}.mp4"
                download_url(work_url, result_path)
                result_obj = f"magnific/automation/{job_id}/result_dreamface_{work_id}.mp4"
                row["result_supabase_url"] = supabase_upload(result_path, result_obj)
                row["provider_status"] = "COMPLETED"
                row["status"] = "COMPLETED"
                break

        recent = state.setdefault("recent_video_ids", [])
        for used_id in [product_video_id, motion_video_id]:
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
    raise RuntimeError("The one-step run command is deprecated for this workflow. Use prepare, OpenClaw image_generate, then complete.")

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
    comp.add_argument("--provider", choices=["magnific", "magnefic", "dreamface", "dream_face", "dream-face"], help="Video provider. Defaults to VIDEO_PROVIDER env or magnific.")
    cleanp = sub.add_parser("cleanup")
    cleanp.add_argument("--dry-run", action="store_true")
    sub.add_parser("format-sheet", help="Apply status dropdown enum and color marks to the Google Sheet.")
    args = ap.parse_args()
    load_env()
    if args.cmd == "run":
        raise SystemExit(run(args.image))
    if args.cmd == "prepare":
        raise SystemExit(prepare(args.image))
    if args.cmd == "complete":
        raise SystemExit(complete(args.job_id, args.generated_reference_path, provider=args.provider))
    if args.cmd == "cleanup":
        removed = cleanup_old(dry_run=args.dry_run)
        print(json.dumps(removed, indent=2))
    if args.cmd == "format-sheet":
        ws = get_sheet()
        ensure_sheet_header(ws)
        print(json.dumps({"status_values": STATUS_VALUES, "formatted": True}, indent=2))


if __name__ == "__main__":
    main()
