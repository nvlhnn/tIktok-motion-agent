import json
import os

from .config import int_env
from .utils import now_utc, indonesia_pretty_datetime, row_uploaded_age_days
from .storage import log_row, load_run_rows
from .tiktok import tiktok_public_stats


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
        try:
            known_views = int(float(r.get("tiktok_views") or 0)) if str(r.get("tiktok_views") or "").strip() else 0
        except (TypeError, ValueError):
            known_views = 0
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
