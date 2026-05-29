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
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageStat

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
    "Keep the motion natural, realistic, vertical social-video style, with stable identity and clean lighting. "
    "Keep the final video in TikTok vertical 9:16 composition matching the motion reference, but do not crop, cut off, zoom in, or remove any important body parts, head, hijab, clothing, product details, hands, or feet. Preserve full subject framing safely within the vertical frame."
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
    "result_supabase_url",
    "caption",
    "provider",
    "provider_auth_label",
    "provider_task_id",
    "provider_result_url",
    "delete_after",
    "error",
    "uploaded_at",
    "scheduled_at",
    "buffer_post_id",
    "buffer_status",
    "buffer_error",
    "external_link",
    "buffer_channel_id",
    "uploaded_via",
    "upload_attempts",
    "tiktok_views",
    "tiktok_likes",
    "tiktok_comments",
    "tiktok_shares",
    "stats_checked_at",
    "product_match_status",
    "product_match_score",
    "product_match_reason",
    "product_match_checked_at",
    "action_needed",
]

STATUS_VALUES = [
    "STARTED",
    "NEEDS_REFERENCE_IMAGE",
    "QUEUED",
    "SUBMITTED",
    "PROCESSING",
    "COMPLETED",
    "READY_TO_UPLOAD",
    "UPLOADING",
    "REJECTED",
    "UPLOADED",
    "UPLOAD_FAILED",
    "READY_TO_AFFILIATE",
    "AFFILIATED",
    "FAILED",
    "TIMEOUT",
]


TERMINAL_STATUSES = {"COMPLETED", "READY_TO_UPLOAD", "REJECTED", "UPLOADED", "UPLOAD_FAILED", "READY_TO_AFFILIATE", "AFFILIATED", "FAILED", "TIMEOUT"}
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
    "UPLOADING": {"backgroundColor": {"red": 0.65, "green": 0.92, "blue": 1.00}},        # cyan
    "REJECTED": {"backgroundColor": {"red": 1.00, "green": 0.60, "blue": 0.60}},         # red
    "UPLOADED": {"backgroundColor": {"red": 0.55, "green": 0.78, "blue": 1.00}},         # stronger blue
    "UPLOAD_FAILED": {"backgroundColor": {"red": 1.00, "green": 0.62, "blue": 0.62}},    # red
    "READY_TO_AFFILIATE": {"backgroundColor": {"red": 1.00, "green": 0.86, "blue": 0.45}}, # gold
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


ID_MONTHS = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def indonesia_pretty_datetime(ts: dt.datetime):
    local = ts.astimezone(ZoneInfo("Asia/Jakarta"))
    return f"{local.day} {ID_MONTHS[local.month - 1]} {local.year} {local:%H:%M} WIB"


def sheet_col(index: int) -> str:
    """Return a Google Sheets column label for a zero-based column index."""
    index += 1
    label = ""
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


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


def ensure_sheet_header(ws, apply_controls: bool = False):
    existing = ws.row_values(1)
    changed = existing != COLUMNS
    if changed:
        end_col = sheet_col(len(COLUMNS) - 1)
        ws.update(f"A1:{end_col}1", [COLUMNS])
        if len(COLUMNS) < 26:
            clear_start = sheet_col(len(COLUMNS))
            ws.batch_clear([f"{clear_start}1:Z1"])
    if changed or apply_controls:
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
        table_range["endColumnIndex"] = len(COLUMNS)
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

    status_col = sheet_col(COLUMNS.index("status"))
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
    status_col = sheet_col(COLUMNS.index("status"))
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
                "provider": row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
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
                "provider": job.get("provider") or job.get("video_provider", ""),
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
                "provider": row.get("provider") or row.get("video_provider") or ("magnific" if row.get("magnific_task_id") else ""),
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


def normalize_provider_fields(row: dict):
    """Map old vendor-specific columns to the provider-neutral schema."""
    row = dict(row)
    provider = row.get("provider") or row.get("video_provider") or ""
    if not provider:
        if row.get("dreamface_animate_id") or row.get("dreamface_work_id"):
            provider = "dreamface"
        elif row.get("magnific_task_id") or row.get("result_magnific_url"):
            provider = "magnific"
    if provider:
        row["provider"] = provider

    row["provider_task_id"] = row.get("provider_task_id") or row.get("provider_job_id") or row.get("dreamface_animate_id") or row.get("magnific_task_id") or ""
    row["provider_work_id"] = row.get("provider_work_id") or row.get("dreamface_work_id") or ""
    row["provider_result_url"] = row.get("provider_result_url") or row.get("result_magnific_url") or ""
    return row


def normalize_row(row: dict):
    row = normalize_provider_fields(row)
    if row.get("product_title") and not row.get("caption"):
        row = dict(row)
        row["caption"] = build_tiktok_caption(row.get("product_title", ""))
    if row.get("status") == "UPLOADED" and not row.get("uploaded_at"):
        row = dict(row)
        row["uploaded_at"] = iso(now_utc())
    return row


CAPTION_STOPWORDS = {
    "ready", "stock", "import", "murah", "premium", "terbaru", "kekinian", "style", "gaya",
    "wanita", "cewek", "perempuan", "baju", "atasan", "outfit", "fashion", "casual", "korea", "korean",
    "by", "dan", "dengan", "untuk", "ukuran", "motif", "variasi", "model", "the", "a", "an",
}


CAPTION_TAG_MAP = {
    "kemeja": ["#kemejawanita", "#atasanwanita", "#fashionwanita"],
    "blouse": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "blus": ["#blousewanita", "#atasanwanita", "#fashionwanita"],
    "sweater": ["#sweaterwanita", "#atasanwanita", "#outfitkekinian"],
    "rajut": ["#sweaterwanita", "#atasanwanita", "#ootd"],
    "knit": ["#sweaterwanita", "#atasanwanita", "#outfitinspiration"],
    "cardigan": ["#cardiganwanita", "#atasanwanita", "#outfitkekinian"],
    "kardigan": ["#cardiganwanita", "#atasanwanita", "#outfitkekinian"],
    "outer": ["#outerwanita", "#atasanwanita", "#ootd"],
    "vest": ["#vestwanita", "#atasanwanita", "#outfitinspiration"],
    "rompi": ["#rompiwanita", "#atasanwanita", "#ootd"],
    "kaos": ["#atasanwanita", "#fashionwanita", "#ootd"],
    "denim": ["#kemejawanita", "#atasanwanita", "#ootd"],
    "jeans": ["#kemejawanita", "#atasanwanita", "#ootd"],
    "crop": ["#atasanwanita", "#outfitkekinian"],
    "babydoll": ["#blousewanita", "#atasanwanita", "#ootd"],
    "bordir": ["#blousewanita", "#kemejawanita", "#atasanwanita"],
    "pita": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "ribbon": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "peplum": ["#blousewanita", "#atasanwanita", "#outfitkekinian"],
    "coquette": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
}

CAPTION_BASE_TAGS = [
    "#atasanwanita",
    "#blouse",
    "#kemejawanita",
    "#blousewanita",
    "#outfitinspiration",
    "#fashionwanita",
    "#outfitkekinian",
    "#ootd",
]


def clean_product_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]", " ", title or "")
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"[^\w\s\-/&]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def caption_keywords(title: str, limit: int = 2) -> list[str]:
    title = clean_product_title(title)
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", title.lower())
    picked = []
    for word in words:
        if len(word) < 4 or word in CAPTION_STOPWORDS or word == "ini":
            continue
        if word not in picked:
            picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def caption_tags(title: str) -> list[str]:
    lower = clean_product_title(title).lower()
    tags = []
    for key, mapped in CAPTION_TAG_MAP.items():
        if key in lower:
            tags.extend(mapped)
    # Competitor pattern: repetitive, broad modest-fashion discovery tags beat clever/random tags.
    tags.extend(CAPTION_BASE_TAGS)
    deduped = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return deduped[:6]


def build_tiktok_caption(product_title: str) -> str:
    title = clean_product_title(product_title)
    lower = title.lower()
    kws = caption_keywords(title)
    if "bordir" in lower:
        text = "bordirnya manis bgt"
    elif "denim" in lower or "jeans" in lower:
        text = "denim gini cakep"
    elif "rajut" in lower or "knit" in lower:
        text = "rajutnya cakep bgt"
    elif "pita" in lower or "ribbon" in lower:
        text = "pitanya gemes bgt"
    elif "outer" in lower or "cardigan" in lower or "kardigan" in lower:
        text = "outer kepake terus"
    elif "kemeja" in lower:
        text = "kemejanya clean bgt"
    elif "blouse" in lower or "blus" in lower:
        text = "blouse simple cakep"
    elif kws:
        text = " ".join(kws[:2] + ["cakep"])
    else:
        text = "simple tapi cakep"
    return f"{text.lower()} {' '.join(caption_tags(title))}".strip()


def caption_for_job(job_id: str | None = None, title: str | None = None) -> dict:
    if title:
        return {"product_title": title, "caption": build_tiktok_caption(title)}
    if not job_id:
        raise RuntimeError("caption needs either job_id or --title")
    state = state_load()
    row = None
    if job_id in (state.get("prepared_jobs") or {}):
        row = (state["prepared_jobs"][job_id] or {}).get("row")
    if row is None and RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            for existing in csv.DictReader(f):
                if existing.get("job_id") == job_id:
                    row = existing
    if not row:
        raise RuntimeError(f"Job not found: {job_id}")
    caption = build_tiktok_caption(row.get("product_title", ""))
    row = dict(row)
    row["caption"] = caption
    upsert_local_csv(row)
    try:
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet caption update skipped: {e}", file=sys.stderr)
    return {"job_id": job_id, "product_title": row.get("product_title", ""), "caption": caption}


def set_caption_for_job(job_id: str, caption: str) -> dict:
    caption = (caption or "").strip()
    if not caption:
        raise RuntimeError("Caption cannot be empty")
    state = state_load()
    row = None
    if job_id in (state.get("prepared_jobs") or {}):
        info = state["prepared_jobs"][job_id]
        row = dict((info or {}).get("row") or {})
        row["caption"] = caption
        info["row"] = row
        state["prepared_jobs"][job_id] = info
        state_save(state)
    if row is None and RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            for existing in csv.DictReader(f):
                if existing.get("job_id") == job_id:
                    row = dict(existing)
                    row["caption"] = caption
                    break
    if row is None:
        raise RuntimeError(f"Job not found: {job_id}")
    upsert_local_csv(row)
    try:
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet caption update skipped: {e}", file=sys.stderr)
    return {"job_id": job_id, "product_title": row.get("product_title", ""), "caption": caption}


def set_status_for_job(job_id: str, status: str, note: str = "") -> dict:
    status = (status or "").strip().upper()
    if status not in STATUS_VALUES:
        raise RuntimeError(f"Invalid status {status!r}; allowed={STATUS_VALUES}")
    state = state_load()
    row = None
    if job_id in (state.get("prepared_jobs") or {}):
        info = state["prepared_jobs"][job_id]
        row = dict((info or {}).get("row") or {})
        row["status"] = status
        if note:
            row["error"] = note
        info["row"] = row
        state["prepared_jobs"][job_id] = info
        state_save(state)
    if row is None and RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            for existing in csv.DictReader(f):
                if existing.get("job_id") == job_id:
                    row = dict(existing)
                    row["status"] = status
                    if note:
                        row["error"] = note
                    break
    if row is None:
        raise RuntimeError(f"Job not found: {job_id}")
    log_row(row)
    return {"job_id": job_id, "status": status, "note": note, "result_link": row.get("result_supabase_url", ""), "caption": row.get("caption", "")}


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
        end_col = sheet_col(len(COLUMNS) - 1)
        ws.update(f"A{row_index}:{end_col}{row_index}", [values])
    else:
        ws.append_row(values, value_input_option="RAW")
        row_index = len(ws.col_values(1))
    status = row.get("status")
    fmt = STATUS_COLORS.get(status)
    if fmt and row_index:
        status_col = sheet_col(COLUMNS.index("status"))
        ws.format(f"{status_col}{row_index}", fmt)


def log_row(row: dict):
    upsert_local_csv(row)
    try:
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet log skipped: {e}", file=sys.stderr)


def load_run_rows(prefer_sheet: bool = True) -> list[dict]:
    if prefer_sheet:
        try:
            ws = get_sheet()
            ensure_sheet_header(ws)
            rows = ws.get_all_records(default_blank="")
            return [normalize_row(dict(r)) for r in rows if dict(r).get("job_id")]
        except Exception as e:
            print(f"sheet read skipped, falling back to local csv: {e}", file=sys.stderr)
    if not RUNS_CSV.exists():
        return []
    with RUNS_CSV.open("r", newline="") as f:
        return [normalize_row(dict(r)) for r in csv.DictReader(f) if r.get("job_id")]


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Invalid integer env {name}={raw!r}")


def truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def upload_slots() -> list[str]:
    raw = os.environ.get("TIKTOK_UPLOAD_SLOTS", "08:00,12:30,16:30,20:30")
    return [s.strip() for s in re.split(r"[,\n]", raw) if s.strip()]


def upload_windows() -> list[str]:
    """Optional randomized upload windows, e.g. 07:45-09:15,11:30-13:00."""
    raw = os.environ.get("TIKTOK_UPLOAD_WINDOWS", "")
    return [s.strip() for s in re.split(r"[,\n]", raw) if s.strip()]


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = [int(x) for x in value.split(":", 1)]
    except Exception:
        raise RuntimeError(f"Invalid time {value!r}; expected HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise RuntimeError(f"Invalid time {value!r}; expected HH:MM")
    return hour, minute


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


def tiktok_public_stats(tiktok_url: str) -> dict:
    ytdlp_python = Path(os.environ.get("YTDLP_PYTHON", "/root/.openclaw/workspace/.venv-ytdlp/bin/python"))
    if not ytdlp_python.exists():
        ytdlp_python = Path(sys.executable)
    cmd = [str(ytdlp_python), "-m", "yt_dlp", "--dump-json", "--skip-download", tiktok_url]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "yt-dlp stats failed").strip()[-1200:])
    data = json.loads(proc.stdout)
    return {
        "view_count": data.get("view_count") or 0,
        "like_count": data.get("like_count") or 0,
        "comment_count": data.get("comment_count") or 0,
        "repost_count": data.get("repost_count") or data.get("share_count") or 0,
        "webpage_url": data.get("webpage_url") or tiktok_url,
    }


def parse_indonesia_pretty_datetime(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"^(\d{1,2})\s+(\w+)\s+(\d{4})\s+(\d{1,2}):(\d{2})\s+WIB$", value)
    if not m:
        try:
            parsed = dt.datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except Exception:
            return None
    day, month_name, year, hour, minute = m.groups()
    try:
        month = ID_MONTHS.index(month_name) + 1
    except ValueError:
        return None
    return dt.datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=ZoneInfo("Asia/Jakarta"))


def row_uploaded_age_days(row: dict) -> int | None:
    uploaded = parse_indonesia_pretty_datetime(row.get("uploaded_at") or row.get("scheduled_at") or "")
    if not uploaded:
        return None
    return int((now_utc() - uploaded.astimezone(dt.timezone.utc)).total_seconds() // 86400)


def affiliate_monitor(update: bool = False, limit: int | None = None) -> dict:
    # Use local CSV as source of truth for Buffer/TikTok external_link because
    # older sheet rows may be missing newly-added upload/affiliate columns.
    rows = load_run_rows(prefer_sheet=False)
    threshold = int_env("AFFILIATE_REVIEW_MIN_VIEWS", 1000)
    max_rows = limit if limit is not None else int_env("AFFILIATE_MONITOR_MAX_ROWS", 50)
    max_age_days = int_env("AFFILIATE_MONITOR_MAX_AGE_DAYS", 30)
    checked = []
    skipped = []
    expired = []
    candidates = []
    errors = []
    review_closed = {"MATCH_STRONG", "MATCH_OK", "MISMATCH_RISK", "MISMATCH_BAD", "NOT_MATCH", "REJECTED"}
    eligible = []
    for r in rows:
        status = (r.get("status") or "").upper()
        if status not in {"UPLOADED", "READY_TO_AFFILIATE"}:
            continue
        if not (r.get("external_link") or "").strip():
            continue
        product_match_status = (r.get("product_match_status") or "").strip().upper()
        if status == "READY_TO_AFFILIATE" or product_match_status in review_closed:
            skipped.append({"job_id": r.get("job_id"), "reason": "affiliate_review_closed", "status": status, "product_match_status": product_match_status})
            continue
        age_days = row_uploaded_age_days(r)
        known_views = int(float(r.get("tiktok_views") or 0)) if str(r.get("tiktok_views") or "").strip() else 0
        if age_days is not None and age_days > max_age_days:
            if known_views < threshold:
                r.update({
                    "product_match_status": "LOW_VIEWS_EXPIRED",
                    "product_match_checked_at": indonesia_pretty_datetime(now_utc()),
                    "action_needed": f"No affiliate: under {threshold} views after {max_age_days} days",
                })
                if update:
                    log_row(r)
                expired.append({"job_id": r.get("job_id"), "age_days": age_days, "views": known_views})
            else:
                skipped.append({"job_id": r.get("job_id"), "reason": "outside_affiliate_date_range", "age_days": age_days, "views": known_views})
            continue
        eligible.append(r)
    for row in eligible[:max_rows]:
        try:
            stats = tiktok_public_stats(row["external_link"].strip())
            now_pretty = indonesia_pretty_datetime(now_utc())
            views = int(stats.get("view_count") or 0)
            row.update({
                "tiktok_views": str(views),
                "tiktok_likes": str(stats.get("like_count") or 0),
                "tiktok_comments": str(stats.get("comment_count") or 0),
                "tiktok_shares": str(stats.get("repost_count") or 0),
                "stats_checked_at": now_pretty,
            })
            if views >= threshold and not (row.get("product_match_status") or "").strip():
                row.update({
                    "product_match_status": "NEEDS_REVIEW",
                    "action_needed": "Review product/video match. If VERY MATCH, set READY_TO_AFFILIATE.",
                })
                candidates.append({
                    "job_id": row.get("job_id"),
                    "views": views,
                    "external_link": row.get("external_link"),
                    "product_title": row.get("product_title"),
                    "product_url": row.get("product_url"),
                    "product_image_url": row.get("product_image_url"),
                    "result_supabase_url": row.get("result_supabase_url"),
                })
            if update:
                log_row(row)
            age_days = row_uploaded_age_days(row)
            if age_days is not None and age_days > max_age_days and views < threshold and not (row.get("product_match_status") or "").strip():
                row.update({
                    "product_match_status": "LOW_VIEWS_EXPIRED",
                    "product_match_checked_at": indonesia_pretty_datetime(now_utc()),
                    "action_needed": f"No affiliate: under {threshold} views after {max_age_days} days",
                })
                expired.append({"job_id": row.get("job_id"), "age_days": age_days, "views": views})
                if update:
                    log_row(row)
            checked.append({"job_id": row.get("job_id"), "views": views, "status": row.get("status"), "product_match_status": row.get("product_match_status", ""), "age_days": age_days})
        except Exception as e:
            errors.append({"job_id": row.get("job_id"), "external_link": row.get("external_link"), "error": str(e)[:500]})
    return {"threshold": threshold, "max_age_days": max_age_days, "update": update, "checked_count": len(checked), "expired_count": len(expired), "skipped_count": len(skipped), "candidates_needing_review": candidates, "expired_low_views": expired, "skipped": skipped, "checked": checked, "errors": errors}


def set_affiliate_review(job_id: str, verdict: str, score: str = "", reason: str = "") -> dict:
    verdict = (verdict or "").strip().upper()
    score = str(score or "").strip()
    reason = (reason or "").strip()
    ready_verdicts = {"MATCH_STRONG", "VERY_MATCH", "READY_TO_AFFILIATE"}
    risk_verdicts = {"MATCH_OK", "MISMATCH_RISK", "MISMATCH_BAD", "NOT_MATCH", "REJECTED"}
    if verdict not in ready_verdicts | risk_verdicts:
        raise RuntimeError(f"Invalid affiliate review verdict: {verdict}")
    row = None
    for existing in load_run_rows(prefer_sheet=False):
        if existing.get("job_id") == job_id:
            row = dict(existing)
            break
    if row is None:
        raise RuntimeError(f"Job not found: {job_id}")
    now_pretty = indonesia_pretty_datetime(now_utc())
    row.update({
        "product_match_status": "MATCH_STRONG" if verdict in ready_verdicts else verdict,
        "product_match_score": score,
        "product_match_reason": reason,
        "product_match_checked_at": now_pretty,
    })
    if verdict in ready_verdicts:
        row["status"] = "READY_TO_AFFILIATE"
        row["action_needed"] = "Add affiliate product link to TikTok VT"
    else:
        # If a previously-ready row is later downgraded, remove it from the
        # affiliate-ready queue. Use COMPLETED for non-affiliate/mismatch rows
        # so they are no longer actionable for affiliate linking.
        row["status"] = "COMPLETED"
        row["action_needed"] = "Do not affiliate automatically; product/video match is not strong"
    log_row(row)
    return {"job_id": job_id, "status": row.get("status"), "product_match_status": row.get("product_match_status"), "action_needed": row.get("action_needed"), "external_link": row.get("external_link")}


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


def safe_cache_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "default"


def yt_dlp_entries(limit=150, profile_url: str | None = None, cache_path: Path | None = None):
    cache_seconds = int(os.environ.get("TIKTOK_LIST_CACHE_SECONDS", "21600"))
    profile = profile_url or require_env("TIKTOK_PROFILE_URL")
    cache_path = cache_path or TIKTOK_LIST_CACHE_PATH
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            age = time.time() - float(cached.get("fetched_at", 0))
            entries = cached.get("entries") or []
            if age < cache_seconds and entries:
                return entries[:limit]
        except Exception:
            pass

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
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"profile_url": profile, "fetched_at": time.time(), "entries": entries}, ensure_ascii=False) + "\n")
    return entries


def motion_profile_urls() -> list[str]:
    raw = os.environ.get("TIKTOK_MOTION_PROFILE_URLS", "").strip()
    if not raw:
        return [require_env("TIKTOK_PROFILE_URL")]
    return [u.strip() for u in re.split(r"[,\n]", raw) if u.strip()]


def product_profile_urls() -> list[str]:
    raw = os.environ.get("TIKTOK_PRODUCT_PROFILE_URLS", "").strip()
    if not raw:
        return [require_env("TIKTOK_PROFILE_URL")]
    return [u.strip() for u in re.split(r"[,\n]", raw) if u.strip()]


def tiktok_video_url(entry: dict, fallback_profile_url: str | None = None) -> str:
    if entry.get("webpage_url"):
        return entry["webpage_url"]
    if entry.get("url") and str(entry["url"]).startswith("http"):
        return entry["url"]
    uploader_url = entry.get("uploader_url") or fallback_profile_url or require_env("TIKTOK_PROFILE_URL")
    return f"{uploader_url.rstrip('/')}/video/{entry['id']}"


def video_candidates(state):
    entries = yt_dlp_entries()
    if not entries:
        raise RuntimeError("Could not list TikTok profile videos")
    avoid_count = int(os.environ.get("RECENT_VIDEO_AVOID_COUNT", "10"))
    recent = set(state.get("recent_video_ids", [])[-avoid_count:])
    candidates = [e for e in entries if e.get("id") not in recent] or entries
    random.shuffle(candidates)
    return candidates


def product_video_candidates(state):
    avoid_count = int(os.environ.get("RECENT_VIDEO_AVOID_COUNT", "10"))
    recent = set(state.get("recent_video_ids", [])[-avoid_count:])
    candidates = []
    for profile in product_profile_urls():
        cache_path = DATA_DIR / f"tiktok_entries_cache_product_{safe_cache_name(profile)}.json"
        for entry in yt_dlp_entries(profile_url=profile, cache_path=cache_path):
            if entry.get("id") in recent:
                continue
            item = dict(entry)
            item["_profile_url"] = profile
            candidates.append(item)
    if not candidates:
        for profile in product_profile_urls():
            cache_path = DATA_DIR / f"tiktok_entries_cache_product_{safe_cache_name(profile)}.json"
            for entry in yt_dlp_entries(profile_url=profile, cache_path=cache_path):
                item = dict(entry)
                item["_profile_url"] = profile
                candidates.append(item)
    if not candidates:
        raise RuntimeError("Could not list TikTok product profile videos")
    random.shuffle(candidates)
    return candidates


def entry_duration_seconds(entry: dict) -> float | None:
    for key in ["duration", "duration_string"]:
        raw = entry.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(str(raw).strip())
        except Exception:
            pass
    return None


def recent_motion_video_ids_from_runs(limit: int) -> set[str]:
    if limit <= 0 or not RUNS_CSV.exists():
        return set()
    try:
        with RUNS_CSV.open(newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return set()
    ids = []
    for row in rows:
        url = row.get("motion_tiktok_video_url") or ""
        m = re.search(r"/video/(\d+)", url)
        if m:
            ids.append(m.group(1))
    return set(ids[-limit:])


def motion_video_candidates(state):
    avoid_count = int(os.environ.get("RECENT_VIDEO_AVOID_COUNT", "10"))
    motion_avoid_count = int(os.environ.get("TIKTOK_MOTION_AVOID_COUNT", "120"))
    min_duration = float(os.environ.get("TIKTOK_MOTION_MIN_DURATION_SECONDS", "10"))
    recent = set(state.get("recent_video_ids", [])[-avoid_count:])
    recent.update(recent_motion_video_ids_from_runs(motion_avoid_count))

    def add_candidates(skip_recent: bool) -> list[dict]:
        picked = []
        for profile in motion_profile_urls():
            cache_path = DATA_DIR / f"tiktok_entries_cache_motion_{safe_cache_name(profile)}.json"
            for entry in yt_dlp_entries(profile_url=profile, cache_path=cache_path):
                if skip_recent and entry.get("id") in recent:
                    continue
                duration = entry_duration_seconds(entry)
                # yt-dlp flat playlist already gives duration for TikTok profiles, so
                # reject short motion refs before doing the expensive video download.
                if duration is not None and duration < min_duration:
                    continue
                item = dict(entry)
                item["_profile_url"] = profile
                picked.append(item)
        return picked

    candidates = add_candidates(skip_recent=True) or add_candidates(skip_recent=False)
    if not candidates:
        raise RuntimeError(f"Could not list TikTok motion profile videos >= {min_duration:g}s")
    random.shuffle(candidates)
    return candidates


def pick_video(state):
    candidates = video_candidates(state)
    return candidates[0]


def pick_different_motion_video(state, excluded_video_id: str):
    candidates = [e for e in motion_video_candidates(state) if e.get("id") != excluded_video_id]
    if not candidates:
        raise RuntimeError("Could not find a motion video different from the capture video")
    return candidates[0]


def pick_video_with_product(state):
    candidates = product_video_candidates(state)
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
            tiktok_url = tiktok_video_url(entry, entry.get("_profile_url"))
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
    if product_id:
        # TikTok affiliate/showcase accepts the TikTok product URL format, not
        # the Tokopedia PDP/anchor URL that is embedded in some video cards.
        seo_url = f"https://www.tiktok.com/view/product/{product_id}"
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


def download_tiktok_video_with_ytdlp(video_id: str, tiktok_url: str, job_dir: Path, reason: Exception | None = None):
    """Fallback downloader when tikwm is temporarily down/flaky."""
    out = job_dir / f"tiktok_{video_id}.mp4"
    ytdlp_python = Path(os.environ.get("YTDLP_PYTHON", "/root/.openclaw/workspace/.venv-ytdlp/bin/python"))
    if not ytdlp_python.exists():
        ytdlp_python = Path(sys.executable)
    cmd = [
        str(ytdlp_python), "-m", "yt_dlp",
        "--no-playlist",
        "--force-overwrites",
        "-f", "bv*+ba/best",
        "-o", str(out),
        tiktok_url,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=240)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        if reason:
            raise RuntimeError(f"tikwm failed ({reason}); yt-dlp fallback also failed: {detail}")
        raise RuntimeError(f"yt-dlp fallback failed: {detail}")
    if not out.exists() or out.stat().st_size < 100 * 1024:
        raise RuntimeError("Downloaded TikTok video is suspiciously small after yt-dlp fallback")
    product_url, product_title, _ = extract_product_from_html(tiktok_url)
    return out, {"fallback": "yt-dlp", "tikwm_error": str(reason or "")}, product_url, product_title


def download_tiktok_video(video_id: str, tiktok_url: str, job_dir: Path):
    """Download a TikTok video, preferring tikwm and falling back to yt-dlp.

    Product URL/title are parsed separately from TikTok HTML, because tikwm does
    not consistently expose affiliate anchors.
    """
    try:
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
    except Exception as e:
        print(f"tikwm download failed, trying yt-dlp fallback: {e}", file=sys.stderr)
        return download_tiktok_video_with_ytdlp(video_id, tiktok_url, job_dir, reason=e)


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
    return status_code in {403, 500, 502, 503, 504} or "blocked" in lowered or "rate" in lowered


class MagnificApiError(RuntimeError):
    def __init__(self, status_code: int | None, data: dict, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


def magnific_auths() -> list[dict]:
    """Load Magnific auth pool from env.

    Preferred env shape:
      MAGNIFIC_AUTHS_JSON='[{"label":"magnific-1","api_key":"FPSX..."}]'

    Also supported:
      MAGNIFIC_API_KEYS_JSON='["FPSX...", {"label":"m2","api_key":"FPSX..."}]'
      MAGNIFIC_API_KEYS='FPSX...,FPSX...'
      MAGNIFIC_API_KEY='FPSX...'
    """
    auths: list[dict] = []
    raw = os.environ.get("MAGNIFIC_AUTHS_JSON", "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("auths") or parsed.get("accounts") or []
        if not isinstance(parsed, list):
            raise RuntimeError("MAGNIFIC_AUTHS_JSON must be a JSON list or object with auths/accounts")
        auths = [dict(a) for a in parsed]
    else:
        keys_raw = os.environ.get("MAGNIFIC_API_KEYS_JSON", "").strip()
        if len(keys_raw) >= 2 and keys_raw[0] == keys_raw[-1] and keys_raw[0] in {"'", '"'}:
            keys_raw = keys_raw[1:-1]
        if keys_raw:
            parsed = json.loads(keys_raw)
            if not isinstance(parsed, list):
                raise RuntimeError("MAGNIFIC_API_KEYS_JSON must be a JSON list")
            for idx, item in enumerate(parsed, start=1):
                if isinstance(item, str):
                    auths.append({"label": f"magnific-{idx}", "api_key": item})
                elif isinstance(item, dict):
                    auths.append(dict(item))
                else:
                    raise RuntimeError("MAGNIFIC_API_KEYS_JSON entries must be strings or objects")
        elif os.environ.get("MAGNIFIC_API_KEYS"):
            for idx, key in enumerate([k.strip() for k in os.environ["MAGNIFIC_API_KEYS"].split(",") if k.strip()], start=1):
                auths.append({"label": f"magnific-{idx}", "api_key": key})
        elif os.environ.get("MAGNIFIC_API_KEY"):
            auths.append({"label": os.environ.get("MAGNIFIC_AUTH_LABEL") or "magnific-1", "api_key": os.environ["MAGNIFIC_API_KEY"]})

    cleaned = []
    for idx, auth in enumerate(auths, start=1):
        auth.setdefault("label", f"magnific-{idx}")
        if not auth.get("api_key"):
            raise RuntimeError(f"Magnific auth {auth.get('label')!r} missing api_key")
        cleaned.append(auth)
    if not cleaned:
        raise RuntimeError("No Magnific auth configured. Set MAGNIFIC_AUTHS_JSON, MAGNIFIC_API_KEYS, or MAGNIFIC_API_KEY.")
    return cleaned


def magnific_auth_by_label(label: str | None) -> dict | None:
    if not label:
        return None
    for auth in magnific_auths():
        if auth.get("label") == label:
            return auth
    return None


def is_magnific_quota_error(error: Exception) -> bool:
    if not isinstance(error, MagnificApiError):
        return False
    text = json.dumps(error.data, ensure_ascii=False).lower()
    return error.status_code == 429 or "limit" in text or "quota" in text or "free trial" in text


def remove_magnific_auth_from_env(limited_auth: dict):
    """Remove a quota-limited Magnific key from ROOT/.env for future runs.

    Supports every auth-pool env shape accepted by magnific_auths(). The current
    process keeps its already-loaded in-memory auth list; this persists the
    removal so the next generation/retry will not reuse a dead key.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    key = limited_auth.get("api_key")
    label = limited_auth.get("label")
    if not key:
        return

    changed = False
    output = []
    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        raw = value.strip()
        unquoted = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'} else raw

        if name == "MAGNIFIC_AUTHS_JSON":
            try:
                parsed = json.loads(unquoted)
                wrapper_key = None
                if isinstance(parsed, dict):
                    wrapper_key = "auths" if "auths" in parsed else "accounts" if "accounts" in parsed else None
                    auths = parsed.get(wrapper_key) if wrapper_key else []
                else:
                    auths = parsed
                if isinstance(auths, list):
                    kept = [a for a in auths if not (isinstance(a, dict) and (a.get("api_key") == key or (label and a.get("label") == label)))]
                    changed = changed or len(kept) != len(auths)
                    if wrapper_key:
                        parsed[wrapper_key] = kept
                        output.append(f"{name}={json.dumps(parsed, separators=(',', ':'))}")
                    else:
                        output.append(f"{name}={json.dumps(kept, separators=(',', ':'))}")
                    continue
            except Exception:
                pass

        if name == "MAGNIFIC_API_KEYS_JSON":
            try:
                parsed = json.loads(unquoted)
                if isinstance(parsed, list):
                    kept = []
                    for item in parsed:
                        if isinstance(item, str) and item == key:
                            changed = True
                            continue
                        if isinstance(item, dict) and (item.get("api_key") == key or (label and item.get("label") == label)):
                            changed = True
                            continue
                        kept.append(item)
                    output.append(f"{name}={json.dumps(kept, separators=(',', ':'))}")
                    continue
            except Exception:
                pass

        if name == "MAGNIFIC_API_KEYS":
            keys = [k.strip() for k in raw.split(",") if k.strip()]
            kept = [k for k in keys if k != key]
            changed = changed or len(kept) != len(keys)
            output.append(f"{name}={','.join(kept)}")
            continue

        if name == "MAGNIFIC_API_KEY" and raw == key:
            changed = True
            continue

        output.append(line)

    if changed:
        env_path.write_text("\n".join(output).rstrip() + "\n")
        print(f"Removed quota-limited Magnific auth from .env: {label or 'unlabeled'}", file=sys.stderr)


def magnific_post(payload: dict, auth: dict | None = None) -> dict:
    auth = auth or magnific_auths()[0]
    api_key = auth["api_key"]
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
        raise MagnificApiError(r.status_code, data, last_detail)
    raise MagnificApiError(None, {}, last_detail or "Magnific function failed")


def magnific_generate_with_rotation(payload: dict, preferred_label: str | None = None) -> tuple[dict, dict]:
    auths = magnific_auths()
    if preferred_label:
        preferred = magnific_auth_by_label(preferred_label)
        if not preferred:
            raise RuntimeError(f"Configured Magnific auth not found for label: {preferred_label}")
        auths = [preferred]

    quota_errors = []
    for auth in auths:
        try:
            return magnific_post(payload, auth=auth), auth
        except Exception as e:
            if is_magnific_quota_error(e):
                quota_errors.append({"label": auth.get("label"), "error": str(e)})
                remove_magnific_auth_from_env(auth)
                continue
            raise
    raise RuntimeError(json.dumps({
        "ok": False,
        "code": "MAGNIFIC_QUOTA_EXHAUSTED",
        "message": "All configured Magnific API keys are quota-limited.",
        "auths": quota_errors,
    }, ensure_ascii=False))


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


def dreamface_remaining_credits(auth: dict) -> dict:
    """Return DreamFace paid credit balances from the credits endpoint."""
    body = {
        "user_id": auth["user_id"],
        "account_id": auth["account_id"],
        "time_zone": os.environ.get("DREAMFACE_TIME_ZONE", "Asia/Jakarta"),
    }
    data = dreamface_request(
        "POST",
        "/dw-server/credits/get_remaining_credits",
        auth,
        json_body=body,
        referer="https://www.dreamfaceapp.com/home",
    )
    return data.get("data") or {}


def dreamface_combined_quota(auth: dict) -> dict:
    """Return quota using both DreamFace sources.

    Base free quota comes from /dw-server/rights/get_free_rights remain_count.
    Additional usable quota comes from /dw-server/credits/get_remaining_credits
    free_count, per current DreamFace account behavior.
    """
    free = dreamface_quota(auth)
    credits = dreamface_remaining_credits(auth)
    free_count = int(free.get("remain_count") or 0)
    credits_free_count = int(credits.get("free_count") or 0)
    return {
        "quota_source": "rights/get_free_rights + credits/get_remaining_credits",
        "free_remain_count": free_count,
        "free_total_count": free.get("total_count"),
        "credits_free_count": credits_free_count,
        "credits_paid_count": credits.get("paid_count"),
        "credits_free_expires_time": credits.get("free_expires_time"),
        "available_count": free_count + credits_free_count,
        "free_quota": free,
        "credits_quota": credits,
    }


def dreamface_available_count(quota: dict) -> int:
    if "available_count" in quota:
        return int(quota.get("available_count") or 0)
    if "remain_count" in quota:
        return int(quota.get("remain_count") or 0)
    if "paid_count" in quota or "free_count" in quota:
        return int(quota.get("paid_count") or 0) + int(quota.get("free_count") or 0)
    return 0


def select_dreamface_auth() -> tuple[dict, dict]:
    exhausted = []
    for auth in dreamface_auths():
        quota = dreamface_combined_quota(auth)
        remain = dreamface_available_count(quota)
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
    return int(os.environ.get("DREAMFACE_MAX_WAIT_SECONDS", "3600"))


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
    row = {"created_at": indonesia_pretty_datetime(created), "job_id": job_id, "status": "STARTED", "delete_after": delete_after}
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
        product_tiktok_url = tiktok_video_url(product_entry, product_entry.get("_profile_url"))
        row["product_video_url"] = product_tiktok_url
        row["product_url"] = picked_product_url
        row["product_title"] = picked_product_title
        row["caption"] = build_tiktok_caption(picked_product_title)
        if not row["product_url"]:
            raise RuntimeError("Selected TikTok product video has no extractable affiliate/product URL")

        motion_entry = pick_different_motion_video(state, product_video_id)
        motion_video_id = motion_entry["id"]
        motion_tiktok_url = tiktok_video_url(motion_entry, motion_entry.get("_profile_url"))
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
            "product_title": row.get("product_title", ""),
            "caption": row.get("caption", ""),
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


def validate_generated_reference_image(ref_path: Path) -> dict:
    """Validate the generated try-on input image before video submission.

    This is a hard, local gate for objective checks. The agent should still do a
    visual/product/modesty review before calling complete.
    """
    ref_path = Path(ref_path).expanduser().resolve()
    if not ref_path.exists():
        raise RuntimeError(f"Generated reference image not found: {ref_path}")
    if not ref_path.is_file():
        raise RuntimeError(f"Generated reference path is not a file: {ref_path}")
    if ref_path.stat().st_size <= 0:
        raise RuntimeError(f"Generated reference image is empty: {ref_path}")

    min_width = int_env("GENERATED_REFERENCE_MIN_WIDTH", 1080)
    min_height = int_env("GENERATED_REFERENCE_MIN_HEIGHT", 1920)
    allowed_formats = {x.strip().upper() for x in os.environ.get("GENERATED_REFERENCE_FORMATS", "JPEG,JPG,PNG,WEBP").split(",") if x.strip()}
    max_size_mb = float(os.environ.get("GENERATED_REFERENCE_MAX_SIZE_MB", "25"))
    max_size_bytes = int(max_size_mb * 1024 * 1024)
    if ref_path.stat().st_size > max_size_bytes:
        raise RuntimeError(f"Generated reference image too large: {ref_path.stat().st_size} bytes > {max_size_bytes} bytes")

    try:
        with Image.open(ref_path) as img:
            img.verify()
        with Image.open(ref_path) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
            mode = img.mode
            stat_img = img.convert("RGB").resize((64, 64))
            extrema = ImageStat.Stat(stat_img).extrema
    except Exception as e:
        raise RuntimeError(f"Generated reference image is unreadable/corrupt: {e}") from e

    if fmt == "JPG":
        fmt = "JPEG"
    normalized_allowed = {"JPEG" if f == "JPG" else f for f in allowed_formats}
    if normalized_allowed and fmt not in normalized_allowed:
        raise RuntimeError(f"Generated reference format {fmt or 'unknown'} not allowed; allowed={sorted(normalized_allowed)}")
    if width < min_width or height < min_height:
        raise RuntimeError(f"Generated reference too small: {width}x{height}; minimum is {min_width}x{min_height}")
    if width * 16 != height * 9:
        raise RuntimeError(f"Generated reference must be exact 9:16, got {width}x{height}")
    if all((hi - lo) < 3 for lo, hi in extrema):
        raise RuntimeError("Generated reference appears blank/near-solid color")

    return {
        "ok": True,
        "path": str(ref_path),
        "format": fmt,
        "mode": mode,
        "width": width,
        "height": height,
        "aspect": "9:16",
        "size_bytes": ref_path.stat().st_size,
    }


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
    row = normalize_provider_fields(info.get("row", {}))
    job_dir = Path(info["job_dir"])
    motion_video_id = info.get("motion_video_id") or info.get("video_id")
    product_video_id = info.get("product_video_id") or info.get("capture_video_id")
    provider_name = selected_video_provider(provider)
    try:
        reference_validation = validate_generated_reference_image(ref_path)
        row["action_needed"] = "Generated reference validated before video submission"
        row["status"] = "SUBMITTED"
        row["error"] = ""
        row["provider"] = provider_name
        row["input_image_validation"] = json.dumps(reference_validation, ensure_ascii=False)
        prepared[job_id] = {**info, "row": row}
        state_save(state)
        log_row(row)

        gen_ref_obj = f"magnific/automation/{job_id}/generated_reference{ref_path.suffix.lower() or '.png'}"
        row["input_image_url"] = row.get("input_image_url") or supabase_upload(ref_path, gen_ref_obj)

        if provider_name == "magnific":
            task_id = row.get("provider_task_id") or row.get("magnific_task_id")
            selected_auth = magnific_auth_by_label(row.get("provider_auth_label")) or magnific_auths()[0]
            if not task_id:
                gen, selected_auth = magnific_generate_with_rotation({
                    "action": "generate",
                    "image_url": row["input_image_url"],
                    "video_url": row["motion_supabase_video_url"],
                    "character_orientation": "video",
                    "cfg_scale": 0.5,
                    "prompt": DEFAULT_PROMPT,
                }, preferred_label=row.get("provider_auth_label") or None)
                row["provider_auth_label"] = selected_auth.get("label", "")
                task_id = (gen.get("data") or {}).get("task_id")
                row["provider_task_id"] = task_id or ""
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
                status = magnific_post({"action": "status", "task_id": task_id}, auth=selected_auth)
                d = status.get("data") or status
                state_value = d.get("status")
                row["provider_status"] = state_value or ""
                prepared[job_id] = {**info, "row": row}
                state_save(state)
                if state_value == "COMPLETED":
                    generated = d.get("generated") or []
                    if not generated:
                        raise RuntimeError(f"Completed but no generated URL: {status}")
                    row["provider_result_url"] = generated[0]
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
                available = dreamface_available_count(quota)
                free_total = quota.get("free_total_count") or quota.get("total_count") or "?"
                row["provider_status"] = f"quota {available} available (rights {quota.get('free_remain_count', quota.get('remain_count', '?'))}/{free_total} + credits.free {quota.get('credits_free_count', 0)})"

            animate_id = row.get("provider_task_id") or row.get("dreamface_animate_id")
            if not animate_id:
                animate_id = dreamface_submit(selected_auth, row["input_image_url"], row["motion_supabase_video_url"])
                row["provider_task_id"] = animate_id
            row["status"] = "PROCESSING"
            row["provider_status"] = "PROCESSING"
            row["error"] = ""
            prepared[job_id] = {**info, "row": row}
            state_save(state)
            log_row(row)

            started_at = time.time()
            work_id = row.get("provider_work_id") or row.get("dreamface_work_id")
            while True:
                if time.time() - started_at > max_dreamface_wait_seconds():
                    raise TimeoutError(f"DreamFace task timed out: {animate_id}")
                time.sleep(min(dreamface_poll_interval_seconds(), max(1, max_dreamface_wait_seconds() - int(time.time() - started_at))))
                item = dreamface_recent_creation(selected_auth, animate_id)
                if item:
                    row["provider_status"] = str(item.get("web_work_status", ""))
                    if item.get("animate_id") and item.get("animate_id") != animate_id and os.environ.get("DREAMFACE_RECENT_SIZE", "1") != "1":
                        prepared[job_id] = {**info, "row": row}
                        state_save(state)
                        log_row(row)
                        continue
                    work_id = item.get("id") or work_id
                    row["provider_work_id"] = work_id or ""
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
                row["provider_result_url"] = work_url
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
        print(json.dumps({
            "status": "done",
            "job_id": job_id,
            "provider": row.get("provider", ""),
            "result_link": row.get("result_supabase_url", ""),
            "caption": row.get("caption") or build_tiktok_caption(row.get("product_title", "")),
        }, indent=2, ensure_ascii=False))
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
        message = str(e)
        if "Generated reference" in message:
            row["status"] = "NEEDS_REFERENCE_IMAGE"
            row["action_needed"] = "Regenerate generated reference image before video submission"
        else:
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
    valref = sub.add_parser("validate-reference", help="Validate generated try-on input image before video submission.")
    valref.add_argument("generated_reference_path")
    cleanp = sub.add_parser("cleanup")
    cleanp.add_argument("--dry-run", action="store_true")
    capp = sub.add_parser("caption", help="Generate/update TikTok caption from a job product title or explicit title.")
    capp.add_argument("job_id", nargs="?", help="Job id to read product_title from runs/state.")
    capp.add_argument("--title", help="Explicit product title to caption instead of a job id.")
    setcapp = sub.add_parser("set-caption", help="Set an AI-generated TikTok caption for a job.")
    setcapp.add_argument("job_id")
    setcapp.add_argument("caption")
    statp = sub.add_parser("set-status", help="Set job status after manual/AI review.")
    statp.add_argument("job_id")
    statp.add_argument("status", choices=STATUS_VALUES)
    statp.add_argument("--note", default="")
    affp = sub.add_parser("affiliate-monitor", help="Check uploaded TikTok stats and queue posts over AFFILIATE_REVIEW_MIN_VIEWS for product-match review.")
    affp.add_argument("--update", action="store_true", help="Write stats/review queue fields back to CSV/Sheet.")
    affp.add_argument("--limit", type=int, help="Maximum uploaded rows to check.")
    affrp = sub.add_parser("set-affiliate-review", help="Record product/video match verdict and set READY_TO_AFFILIATE when strong.")
    affrp.add_argument("job_id")
    affrp.add_argument("verdict", help="MATCH_STRONG/VERY_MATCH or MISMATCH_RISK/MISMATCH_BAD/etc.")
    affrp.add_argument("--score", default="")
    affrp.add_argument("--reason", default="")
    sub.add_parser("format-sheet", help="Apply status dropdown enum and color marks to the Google Sheet.")
    upp = sub.add_parser("upload-scheduler", help="Pick random READY_TO_UPLOAD rows and publish via Buffer/TikTok. Dry-run by default.")
    upp.add_argument("--live", action="store_true", help="Actually upload. Also requires TIKTOK_UPLOAD_ENABLED=true.")
    upp.add_argument("--dry-run", action="store_true", help="Only show what would upload. This is the default.")
    upp.add_argument("--ignore-slot", action="store_true", help="Allow live upload outside configured slots.")
    upp.add_argument("--test-channel", action="store_true", help="Use BUFFER_TEST_CHANNEL_ID instead of production channel.")
    args = ap.parse_args()
    load_env()
    if args.cmd == "run":
        raise SystemExit(run(args.image))
    if args.cmd == "prepare":
        raise SystemExit(prepare(args.image))
    if args.cmd == "complete":
        raise SystemExit(complete(args.job_id, args.generated_reference_path, provider=args.provider))
    if args.cmd == "validate-reference":
        print(json.dumps(validate_generated_reference_image(Path(args.generated_reference_path)), indent=2, ensure_ascii=False))
    if args.cmd == "cleanup":
        removed = cleanup_old(dry_run=args.dry_run)
        print(json.dumps(removed, indent=2))
    if args.cmd == "caption":
        print(json.dumps(caption_for_job(args.job_id, args.title), indent=2, ensure_ascii=False))
    if args.cmd == "set-caption":
        print(json.dumps(set_caption_for_job(args.job_id, args.caption), indent=2, ensure_ascii=False))
    if args.cmd == "set-status":
        print(json.dumps(set_status_for_job(args.job_id, args.status, args.note), indent=2, ensure_ascii=False))
    if args.cmd == "affiliate-monitor":
        print(json.dumps(affiliate_monitor(update=args.update, limit=args.limit), indent=2, ensure_ascii=False))
    if args.cmd == "set-affiliate-review":
        print(json.dumps(set_affiliate_review(args.job_id, args.verdict, args.score, args.reason), indent=2, ensure_ascii=False))
    if args.cmd == "format-sheet":
        ws = get_sheet()
        ensure_sheet_header(ws, apply_controls=True)
        print(json.dumps({"status_values": STATUS_VALUES, "formatted": True}, indent=2))
    if args.cmd == "upload-scheduler":
        print(json.dumps(upload_scheduler(dry_run=(args.dry_run or not args.live), live=args.live, ignore_slot=args.ignore_slot, test_channel=args.test_channel), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
