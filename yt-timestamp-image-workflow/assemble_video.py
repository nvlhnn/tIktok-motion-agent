#!/usr/bin/env python3
"""Assemble timestamp-named images + WAV audio into a 16:9 MP4.

Expected image filenames:
  Preferred: 001__00-00-00-000.png, 002__00-00-04-100.png, etc.
  Legacy:    0-00.png, 0-03.png, 1-05.png, etc.

Usage:
  python3 assemble_video.py --audio project/audio.wav --images project/images --output project/output.mp4

Optional:
  python3 assemble_video.py --audio audio.wav --images images --script script.txt --output output.mp4

If --script is provided, timestamps are read from the script order.
Otherwise timestamps are inferred from image filenames.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
from pathlib import Path

# Supports loose script timestamps (0:41, 1:05) and transcript timestamps
# with milliseconds (00:00:41,860 or 00:00:41.860).  The start time of a
# transcript/SRT/VTT segment is the visual cut point; end times are ignored.
TS_RE = re.compile(
    r"(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[,.](\d{1,3}))?(?!\d)"
)
EXACT_NAME_RE = re.compile(r"^(\d{3,})__(\d{2})-(\d{2})-(\d{2})-(\d{3})(?:_.+)?\.png$")
LEGACY_NAME_RE = re.compile(r"^(\d+)-(\d{2})(?:-(\d{2}))?(?:_.+)?\.png$")


def parse_ts(text: str) -> float:
    m = TS_RE.search(text)
    if not m:
        raise ValueError(f"No timestamp found in: {text!r}")
    h, mnt, sec, ms = m.groups()
    millis = int((ms or "0").ljust(3, "0")[:3])
    if h is None:
        return int(mnt) * 60 + int(sec) + millis / 1000
    return int(h) * 3600 + int(mnt) * 60 + int(sec) + millis / 1000


def ts_to_name(seconds: float) -> str:
    """Legacy floor-second filename kept for backward compatibility."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}-{m:02d}-{s:02d}.png"
    return f"{m}-{s:02d}.png"


def ts_to_exact_name(index: int, seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h = total_ms // 3_600_000
    total_ms %= 3_600_000
    m = total_ms // 60_000
    total_ms %= 60_000
    s = total_ms // 1000
    ms = total_ms % 1000
    return f"{index:03d}__{h:02d}-{m:02d}-{s:02d}-{ms:03d}.png"


def image_time_from_name(path: Path) -> float | None:
    m = EXACT_NAME_RE.match(path.name)
    if m:
        _idx, h, mn, s, ms = m.groups()
        return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms) / 1000
    m = LEGACY_NAME_RE.match(path.name)
    if not m:
        return None
    a, b, c = m.groups()
    if c is None:
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def audio_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def timestamps_from_script(path: Path) -> list[float]:
    values: list[float] = []
    seen = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = TS_RE.search(line)
        if not m:
            continue
        # In SRT/VTT ranges, the first timestamp is the segment/subject start.
        # The image should transition when the narrator starts the new subject,
        # not when the previous subject ends.
        sec = parse_ts(m.group(0))
        # Preserve order, avoid duplicate accidental repeated timestamp lines.
        key = round(sec, 3)
        if key not in seen:
            values.append(sec)
            seen.add(key)
    if not values:
        raise SystemExit(f"No timestamps found in script: {path}")
    return values


def timestamps_from_images(images_dir: Path) -> list[float]:
    values: list[float] = []
    for p in images_dir.glob("*.png"):
        sec = image_time_from_name(p)
        if sec is None:
            continue
        values.append(sec)
    values = sorted(set(values))
    if not values:
        raise SystemExit(f"No timestamp-named PNGs found in: {images_dir}")
    return values


def image_for_timestamp(images_dir: Path, index: int, seconds: float) -> Path:
    """Find preferred exact filename first, then legacy floor-second filename."""
    exact = images_dir / ts_to_exact_name(index, seconds)
    if exact.exists():
        return exact

    # Be tolerant of optional suffixes after the exact timestamp.
    exact_prefix = ts_to_exact_name(index, seconds).removesuffix(".png")
    matches = sorted(images_dir.glob(f"{exact_prefix}*.png"))
    if matches:
        return matches[0]

    legacy = images_dir / ts_to_name(seconds)
    if legacy.exists():
        return legacy

    legacy_prefix = ts_to_name(seconds).removesuffix(".png")
    matches = sorted(images_dir.glob(f"{legacy_prefix}*.png"))
    if matches:
        return matches[0]

    raise SystemExit(
        f"Missing image for timestamp {seconds:g}s. Expected preferred {exact} "
        f"or legacy {legacy}"
    )


def shell_quote_for_concat(path: Path) -> str:
    # ffmpeg concat accepts single-quoted paths, with inner quote escaped as '\''.
    s = str(path.resolve())
    return "'" + s.replace("'", "'\\''") + "'"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--script", type=Path)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    if not args.audio.exists():
        raise SystemExit(f"Missing audio: {args.audio}")
    if not args.images.exists():
        raise SystemExit(f"Missing images folder: {args.images}")

    duration = audio_duration(args.audio)
    timestamps = timestamps_from_script(args.script) if args.script else timestamps_from_images(args.images)
    timestamps = [t for t in timestamps if t < duration]
    if not timestamps:
        raise SystemExit("No timestamps fall within audio duration.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    concat_path = args.output.with_suffix(".ffmpeg_concat.txt")

    lines: list[str] = []
    final_img: Path | None = None
    for i, start in enumerate(timestamps):
        end = timestamps[i + 1] if i + 1 < len(timestamps) else duration
        img = image_for_timestamp(args.images, i + 1, start)
        final_img = img
        dur = max(0.05, end - start)
        lines.append(f"file {shell_quote_for_concat(img)}")
        lines.append(f"duration {dur:.3f}")
    # Repeat final image for concat demuxer duration handling.
    assert final_img is not None
    lines.append(f"file {shell_quote_for_concat(final_img)}")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    vf = (
        f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease,"
        f"pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(args.audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(args.fps),
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(args.output),
    ]
    print("Running:", " ".join(shlex.quote(x) for x in cmd))
    subprocess.check_call(cmd)
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
