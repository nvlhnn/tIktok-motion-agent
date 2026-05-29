import os
import subprocess
import sys
from pathlib import Path

import requests

from .tiktok import get_tikwm_data, extract_product_from_html, get_first_product_image


def download_url(url: str, path: Path):
    with requests.get(url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024 * 256):
                if chunk:
                    f.write(chunk)


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
