"""Pipeline orchestration — prepare, complete, cleanup, and status management."""

import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from .config import (
    DATA_DIR, DOWNLOADS_DIR, LOGS_DIR, RUNS_CSV,
    COLUMNS, STATUS_VALUES, MODEST_TRYON_PROMPT, DEFAULT_PROMPT,
    load_env, require_env,
)
from .utils import now_utc, iso, indonesia_pretty_datetime
from .state import state_load, state_save, assert_no_active_generation
from .storage import normalize_provider_fields, log_row
from .captions import build_tiktok_caption
from .tiktok import (
    pick_video_with_product, pick_different_motion_video,
    tiktok_video_url,
)
from .downloads import download_product_image, download_url, download_tiktok_video
from .validation import validate_generated_reference_image
from .supabase import supabase_upload, supabase_rm_prefix, supabase_rm_object
from .providers import selected_video_provider
from .providers.magnific import (
    magnific_auths, magnific_auth_by_label,
    magnific_generate_with_rotation, magnific_post,
    check_magnific_timeout, max_magnific_wait_seconds,
)
from .providers.dreamface import (
    dreamface_auths, select_dreamface_auth,
    dreamface_available_count, dreamface_submit,
    dreamface_recent_creation, dreamface_work_detail,
    max_dreamface_wait_seconds, dreamface_poll_interval_seconds,
)


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
    # Only delete full Supabase job folders after the TikTok upload is complete;
    # READY_TO_UPLOAD / REJECTED / READY_TO_AFFILIATE rows may still need the
    # generated reference or final result for review/debug/manual action.
    state = state_load()
    jobs = state.get("jobs", [])
    rows_by_job_id = {}
    try:
        from .storage import load_run_rows
        rows_by_job_id = {r.get("job_id"): r for r in load_run_rows(prefer_sheet=False) if r.get("job_id")}
    except Exception as e:
        print(f"cleanup row-status lookup skipped: {e}", file=sys.stderr)
    kept_jobs = []
    now = now_utc()
    for job in jobs:
        job_id = job.get("job_id") or ""
        delete_after = job.get("delete_after")
        prefix = job.get("supabase_prefix")
        row_status = (rows_by_job_id.get(job_id, {}).get("status") or job.get("status") or "").strip().upper()
        expired = False
        if delete_after:
            try:
                expired = dt.datetime.fromisoformat(delete_after).astimezone(dt.timezone.utc) <= now
            except Exception:
                expired = False
        if expired and prefix and row_status == "UPLOADED":
            removed_supabase.append(supabase_rm_prefix(prefix, dry_run=dry_run))
            if dry_run:
                kept_jobs.append(job)
        else:
            kept_jobs.append(job)
    if not dry_run and kept_jobs != jobs:
        state["jobs"] = kept_jobs
        state_save(state)

    return {"removed_local": removed_local, "removed_supabase": removed_supabase}


def cleanup_supabase_generation_sources(job_id: str, info: dict, row: dict) -> list[dict]:
    """Delete source-only Supabase objects after the final video is saved.

    Keep the final result video. Remove only the product reference image and
    motion source video used as provider inputs, per Naufal's preference.
    """
    object_paths = []
    for key in ["supabase_product_image_object", "motion_supabase_video_object"]:
        value = (info.get(key) or "").strip()
        if value:
            object_paths.append(value)

    # Backward-compatible inference for prepared jobs created before explicit
    # object-path tracking was added.
    if not any("/motion_source_" in p for p in object_paths):
        motion_video_id = info.get("motion_video_id") or info.get("video_id")
        if motion_video_id:
            object_paths.append(f"magnific/automation/{job_id}/motion_source_{motion_video_id}.mp4")
    if not any("/product_reference" in p for p in object_paths):
        product_image_path = info.get("product_image_path") or ""
        suffix = Path(product_image_path).suffix.lower() if product_image_path else ""
        if suffix:
            object_paths.append(f"magnific/automation/{job_id}/product_reference{suffix}")

    deleted = []
    for object_path in dict.fromkeys(object_paths):
        try:
            deleted.append(supabase_rm_object(object_path))
        except Exception as e:
            deleted.append({"object_path": object_path, "error": str(e)[:500]})

    if deleted:
        row["action_needed"] = "Deleted Supabase product reference and motion source after result generation"
        row["motion_supabase_video_url"] = ""
    return deleted


def set_result_retention_deadline(state: dict, job_id: str, row: dict) -> str:
    """Start the 7-day Supabase result/reference retention clock after result upload."""
    delete_after = iso(now_utc() + dt.timedelta(days=int(os.environ.get("RETENTION_DAYS", "7"))))
    row["delete_after"] = delete_after
    for job in state.get("jobs") or []:
        if job.get("job_id") == job_id:
            job["delete_after"] = delete_after
            break
    return delete_after


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
            "supabase_product_image_object": product_image_obj,
            "supabase_product_image_url": supabase_product_image_url,
            "motion_supabase_video_object": motion_video_obj,
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
        set_result_retention_deadline(state, job_id, row)
        source_cleanup = cleanup_supabase_generation_sources(job_id, info, row)
        prepared.pop(job_id, None)
        state_save(state)
        log_row(row)
        print(json.dumps({
            "status": "done",
            "job_id": job_id,
            "provider": row.get("provider", ""),
            "result_link": row.get("result_supabase_url", ""),
            "caption": row.get("caption") or build_tiktok_caption(row.get("product_title", "")),
            "source_cleanup": source_cleanup,
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
