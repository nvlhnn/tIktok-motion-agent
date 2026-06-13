"""Lightweight video processing helpers for final result uploads and input validation."""

import json
import os
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _target_enabled() -> bool:
    return os.environ.get("HF_UPLOAD_720P_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def ffprobe_metadata(path: Path) -> dict:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"Video file not found: {path}")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"ffprobe failed for {path}: {detail}")
    return json.loads(proc.stdout or "{}")


def has_video_stream(path: Path) -> bool:
    meta = ffprobe_metadata(path)
    return any((stream or {}).get("codec_type") == "video" for stream in meta.get("streams") or [])


def assert_video_has_video_stream(path: Path, label: str = "video") -> None:
    if not has_video_stream(path):
        raise RuntimeError(f"{label} is invalid: no video stream found ({Path(path).expanduser().resolve()})")


def hf_upload_720p_target() -> tuple[int, int]:
    """Return the fixed vertical 9:16 upload target.

    The TikTok Motion workflow guarantees 9:16 outputs, so keeping this exact
    target is faster and simpler than doing crop/pad detection on every run.
    """
    width = int(os.environ.get("HF_UPLOAD_WIDTH", "720"))
    height = int(os.environ.get("HF_UPLOAD_HEIGHT", "1280"))
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid HF upload target: {width}x{height}")
    return width, height


def prepare_hf_upload_video_720p(source_path: Path, output_dir: Path | None = None) -> Path:
    """Normalize a final result video to efficient 720p vertical MP4 for HF.

    Uses FFmpeg/Lanczos scaling and stream-copies audio. If disabled via
    HF_UPLOAD_720P_ENABLED=false, returns the source path unchanged.
    """
    source_path = Path(source_path).expanduser().resolve()
    if not _target_enabled():
        return source_path
    if not source_path.exists():
        raise RuntimeError(f"Video source not found: {source_path}")

    width, height = hf_upload_720p_target()
    output_dir = Path(output_dir or source_path.parent).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source_path.stem}_{width}x{height}{source_path.suffix.lower() or '.mp4'}"

    if output_path.resolve() == source_path:
        output_path = output_dir / f"{source_path.stem}_hf720.mp4"

    preset = os.environ.get("HF_UPLOAD_X264_PRESET", "veryfast").strip() or "veryfast"
    crf = os.environ.get("HF_UPLOAD_X264_CRF", "20").strip() or "20"

    _run([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vf",
        f"scale={width}:{height}:flags=lanczos,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "copy",
        str(output_path),
    ])
    return output_path
