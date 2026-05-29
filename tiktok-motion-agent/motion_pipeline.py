#!/usr/bin/env python3
"""TikTok Motion Agent — CLI entry point.

This is a thin dispatcher. All business logic lives in the pipeline/ package.
"""
import argparse
import json
import sys
from pathlib import Path

from pipeline.config import load_env, STATUS_VALUES
from pipeline.orchestrator import (
    prepare, complete, run,
    set_status_for_job, cleanup_old,
)
from pipeline.captions import caption_for_job, set_caption_for_job
from pipeline.validation import validate_generated_reference_image
from pipeline.affiliate import affiliate_monitor, set_affiliate_review
from pipeline.upload import upload_scheduler
from pipeline.sheets import get_sheet, ensure_sheet_header


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
