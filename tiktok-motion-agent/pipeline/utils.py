import datetime as dt
import re
from zoneinfo import ZoneInfo


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


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = [int(x) for x in value.split(":", 1)]
    except Exception:
        raise RuntimeError(f"Invalid time {value!r}; expected HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise RuntimeError(f"Invalid time {value!r}; expected HH:MM")
    return hour, minute


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


def safe_cache_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "default"


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
