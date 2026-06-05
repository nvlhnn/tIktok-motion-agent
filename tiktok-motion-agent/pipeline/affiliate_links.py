import csv
import os
import re
import sys
from pathlib import Path

import requests

from .config import DATA_DIR, require_env, truthy_env
from .utils import indonesia_pretty_datetime, now_utc

AFFILIATE_LINK_COLUMNS = [
    "product_key",
    "tiktok_product_url",
    "product_name",
    "shopee_affiliate_url",
    "created_at",
    "updated_at",
    "notes",
]

AFFILIATE_LINKS_CSV = DATA_DIR / "affiliate_links.csv"
AFFILIATE_SHEET_TITLE = os.environ.get("AFFILIATE_LINKS_SHEET_TITLE", "affiliate_links")


def normalize_product_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url = url.split("#", 1)[0]
    url = re.sub(r"[?&](?:utm_[^=&]+|fbclid|gclid|ttclid)=[^&]*", "", url)
    url = url.rstrip("?&/")
    return url


def product_key(product_url: str) -> str:
    url = normalize_product_url(product_url)
    if not url:
        return ""
    m = re.search(r"/product/(\d+)", url)
    if m:
        return f"tiktok_product:{m.group(1)}"
    m = re.search(r"(?:product_id|productId|item_id|itemId)=([0-9A-Za-z_-]+)", url)
    if m:
        return f"tiktok_product:{m.group(1)}"
    return url.lower()


def normalize_affiliate_link_row(row: dict) -> dict:
    row = dict(row or {})
    row["tiktok_product_url"] = normalize_product_url(row.get("tiktok_product_url") or row.get("product_url") or "")
    row["product_key"] = row.get("product_key") or product_key(row["tiktok_product_url"])
    return {c: str(row.get(c, "") or "") for c in AFFILIATE_LINK_COLUMNS}


def _read_local_affiliate_links() -> list[dict]:
    if not AFFILIATE_LINKS_CSV.exists():
        return []
    with AFFILIATE_LINKS_CSV.open("r", newline="") as f:
        return [normalize_affiliate_link_row(r) for r in csv.DictReader(f) if r.get("product_key") or r.get("tiktok_product_url")]


def _write_local_affiliate_links(rows: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with AFFILIATE_LINKS_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AFFILIATE_LINK_COLUMNS)
        w.writeheader()
        w.writerows([normalize_affiliate_link_row(r) for r in rows])


def get_affiliate_sheet(create: bool = True):
    try:
        import gspread  # noqa: F401
        from .sheets import get_spreadsheet
        sh = get_spreadsheet()
        try:
            return sh.worksheet(AFFILIATE_SHEET_TITLE)
        except Exception:
            if not create:
                raise
            return sh.add_worksheet(title=AFFILIATE_SHEET_TITLE, rows=1000, cols=len(AFFILIATE_LINK_COLUMNS))
    except Exception as e:
        raise RuntimeError(f"affiliate sheet unavailable: {e}")


def ensure_affiliate_sheet_header(ws):
    existing = ws.row_values(1)
    if existing != AFFILIATE_LINK_COLUMNS:
        from .utils import sheet_col
        end_col = sheet_col(len(AFFILIATE_LINK_COLUMNS) - 1)
        ws.update(f"A1:{end_col}1", [AFFILIATE_LINK_COLUMNS])


def load_affiliate_links(prefer_sheet: bool = True) -> list[dict]:
    if prefer_sheet:
        try:
            ws = get_affiliate_sheet(create=True)
            ensure_affiliate_sheet_header(ws)
            return [normalize_affiliate_link_row(r) for r in ws.get_all_records(default_blank="") if r.get("product_key") or r.get("tiktok_product_url")]
        except Exception as e:
            print(f"affiliate sheet read skipped, falling back to local csv: {e}", file=sys.stderr)
    return _read_local_affiliate_links()


def find_affiliate_link(product_url: str, rows: list[dict] | None = None) -> dict | None:
    key = product_key(product_url)
    if not key:
        return None
    rows = rows if rows is not None else load_affiliate_links(prefer_sheet=True)
    for row in rows:
        if row.get("product_key") == key and (row.get("shopee_affiliate_url") or "").strip():
            return row
    return None


def upsert_affiliate_link(product_url: str, shopee_affiliate_url: str = "", product_name: str = "", notes: str = "", prefer_sheet: bool = True) -> dict:
    now = indonesia_pretty_datetime(now_utc())
    key = product_key(product_url)
    if not key:
        raise RuntimeError("Cannot save affiliate link without product_url/product_key")
    incoming = normalize_affiliate_link_row({
        "product_key": key,
        "tiktok_product_url": product_url,
        "product_name": product_name,
        "shopee_affiliate_url": shopee_affiliate_url,
        "updated_at": now,
        "notes": notes,
    })
    rows = load_affiliate_links(prefer_sheet=prefer_sheet)
    replaced = False
    for row in rows:
        if row.get("product_key") == key:
            row.update({k: v for k, v in incoming.items() if v or k in {"updated_at", "notes"}})
            if not row.get("created_at"):
                row["created_at"] = now
            replaced = True
            incoming = normalize_affiliate_link_row(row)
            break
    if not replaced:
        incoming["created_at"] = now
        rows.append(incoming)

    _write_local_affiliate_links(rows)
    if prefer_sheet:
        try:
            ws = get_affiliate_sheet(create=True)
            ensure_affiliate_sheet_header(ws)
            values = [incoming.get(c, "") for c in AFFILIATE_LINK_COLUMNS]
            row_index = None
            for idx, value in enumerate(ws.col_values(AFFILIATE_LINK_COLUMNS.index("product_key") + 1), start=1):
                if idx > 1 and value == key:
                    row_index = idx
                    break
            from .utils import sheet_col
            end_col = sheet_col(len(AFFILIATE_LINK_COLUMNS) - 1)
            if row_index:
                ws.update(f"A{row_index}:{end_col}{row_index}", [values])
            else:
                ws.append_row(values, value_input_option="RAW")
        except Exception as e:
            print(f"affiliate sheet write skipped: {e}", file=sys.stderr)
    return incoming


def affiliate_state_for_row(row: dict, affiliate_rows: list[dict] | None = None) -> dict:
    key = product_key(row.get("product_url") or "")
    found = find_affiliate_link(row.get("product_url") or "", affiliate_rows)
    if found:
        return {
            "product_key": key,
            "affiliate_status": "FOUND",
            "shopee_affiliate_url": found.get("shopee_affiliate_url", ""),
            "action_needed": "",
        }
    return {
        "product_key": key,
        "affiliate_status": "MISSING",
        "shopee_affiliate_url": "",
        "action_needed": "Send Shopee affiliate link for this product; posting continues normally.",
    }


def facebook_object_id_from_url(url: str) -> str:
    url = (url or "").strip()
    for pattern in [r"facebook\.com/(?:reel|watch)/([0-9]+)", r"fb\.watch/([^/?#]+)", r"/posts/([0-9_]+)", r"story_fbid=([0-9_]+)"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ""


def instagram_media_id_from_url(url: str) -> str:
    # Instagram Graph comments require an IG media id, not the public shortcode.
    # Store the public URL for traceability, but only comment automatically if an id
    # is supplied separately later or Buffer/Meta exposes one.
    return ""


def _graph_post_comment(object_id: str, message: str, token: str) -> dict:
    resp = requests.post(
        f"https://graph.facebook.com/v20.0/{object_id}/comments",
        data={"message": message, "access_token": token},
        timeout=45,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text[:1000]}
    if resp.status_code >= 400 or payload.get("error"):
        raise RuntimeError(f"Meta comment failed HTTP {resp.status_code}: {payload}")
    return payload


def comment_affiliate_for_row(row: dict, live: bool = False, prefer_sheet: bool = True) -> tuple[dict, dict]:
    """Best-effort affiliate comment layer. Never blocks upload flow."""
    from .storage import log_row

    updated = dict(row)
    aff = affiliate_state_for_row(row, load_affiliate_links(prefer_sheet=prefer_sheet))
    updated["product_key"] = aff["product_key"]
    updated["shopee_affiliate_url"] = aff["shopee_affiliate_url"]
    updated["affiliate_status"] = aff["affiliate_status"]
    result = {"job_id": row.get("job_id"), "product_key": aff["product_key"], "affiliate_status": aff["affiliate_status"], "comments": []}

    if aff["affiliate_status"] != "FOUND":
        updated["fb_comment_status"] = updated.get("fb_comment_status") or "PENDING_LINK"
        updated["ig_comment_status"] = updated.get("ig_comment_status") or "PENDING_LINK"
        updated["action_needed"] = aff["action_needed"]
        log_row(updated)
        result["needs_affiliate_link"] = True
        return updated, result

    link = aff["shopee_affiliate_url"]
    template_context = dict(updated)
    template_context["shopee_affiliate_url"] = link
    message = os.environ.get("AFFILIATE_COMMENT_TEMPLATE", "Link Shopee: {shopee_affiliate_url}").format(**template_context)
    now = indonesia_pretty_datetime(now_utc())

    targets = [
        ("fb", "facebook_post_url", "fb_comment_status", "fb_comment_at", facebook_object_id_from_url, os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")),
        ("ig", "instagram_post_url", "ig_comment_status", "ig_comment_at", instagram_media_id_from_url, os.environ.get("INSTAGRAM_GRAPH_ACCESS_TOKEN", "") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")),
    ]
    for service, url_field, status_field, at_field, id_fn, token in targets:
        url = (updated.get(url_field) or "").strip()
        if not url:
            if not updated.get(status_field):
                updated[status_field] = "PENDING_POST"
            result["comments"].append({"service": service, "status": updated[status_field], "reason": "post_url_missing"})
            continue
        if updated.get(status_field) == "COMMENTED":
            result["comments"].append({"service": service, "status": "COMMENTED", "reason": "already_done"})
            continue
        object_id = id_fn(url)
        if not object_id:
            updated[status_field] = "FAILED"
            updated[f"{service}_comment_error"] = "Cannot derive Meta object/media id from public URL"
            result["comments"].append({"service": service, "status": "FAILED", "reason": updated[f"{service}_comment_error"], "url": url})
            continue
        if not token:
            updated[status_field] = "READY_TO_COMMENT"
            updated[f"{service}_comment_error"] = "Missing Meta access token env; posting flow is not blocked"
            result["comments"].append({"service": service, "status": "READY_TO_COMMENT", "reason": updated[f"{service}_comment_error"], "url": url})
            continue
        if not live or not truthy_env("AFFILIATE_COMMENT_ENABLED", False):
            updated[status_field] = "READY_TO_COMMENT"
            result["comments"].append({"service": service, "status": "READY_TO_COMMENT", "dry_run": True, "url": url, "message": message})
            continue
        try:
            payload = _graph_post_comment(object_id, message, token)
            updated[status_field] = "COMMENTED"
            updated[at_field] = now
            updated[f"{service}_comment_id"] = payload.get("id", "")
            updated[f"{service}_comment_error"] = ""
            result["comments"].append({"service": service, "status": "COMMENTED", "comment_id": payload.get("id", ""), "url": url})
        except Exception as e:
            updated[status_field] = "FAILED"
            updated[f"{service}_comment_error"] = str(e)[:500]
            result["comments"].append({"service": service, "status": "FAILED", "reason": str(e)[:500], "url": url})

    if any(c.get("status") == "COMMENTED" for c in result["comments"]):
        updated["affiliate_status"] = "COMMENTED"
    log_row(updated)
    return updated, result
