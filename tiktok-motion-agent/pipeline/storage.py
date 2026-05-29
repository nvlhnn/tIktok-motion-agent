import csv
import sys
from pathlib import Path

from .config import DATA_DIR, RUNS_CSV, COLUMNS, STATUS_VALUES
from .utils import now_utc, iso


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
    from .captions import build_tiktok_caption
    row = normalize_provider_fields(row)
    if row.get("product_title") and not row.get("caption"):
        row = dict(row)
        row["caption"] = build_tiktok_caption(row.get("product_title", ""))
    if row.get("status") == "UPLOADED" and not row.get("uploaded_at"):
        row = dict(row)
        row["uploaded_at"] = iso(now_utc())
    return row


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


def log_row(row: dict):
    upsert_local_csv(row)
    try:
        from .sheets import upsert_sheet
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet log skipped: {e}", file=sys.stderr)


def load_run_rows(prefer_sheet: bool = True) -> list[dict]:
    if prefer_sheet:
        try:
            from .sheets import get_sheet, ensure_sheet_header
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
