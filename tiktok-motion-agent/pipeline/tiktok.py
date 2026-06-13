import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests

from .config import DATA_DIR, RUNS_CSV, TIKTOK_LIST_CACHE_PATH, require_env
from .utils import now_utc, iso, safe_cache_name, entry_duration_seconds
from .state import state_save


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


def _configured_provider_name() -> str:
    value = (os.environ.get("VIDEO_PROVIDER") or "").strip().lower()
    aliases = {
        "figma_wave": "figmawave",
        "figma-wave": "figmawave",
        "weavy": "figmawave",
        "dream_face": "dreamface",
        "dream-face": "dreamface",
        "magnefic": "magnific",
    }
    return aliases.get(value, value)


def motion_profile_urls() -> list[str]:
    if _configured_provider_name() == "figmawave":
        raw = os.environ.get("TIKTOK_FIGMAWAVE_MOTION_PROFILE_URLS", "").strip()
        if raw:
            return [u.strip() for u in re.split(r"[,\n]", raw) if u.strip()]
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


def banned_motion_video_ids(state: dict | None = None) -> set[str]:
    banned = set((state or {}).get("banned_motion_video_ids") or [])
    ban_file = DATA_DIR / "banned_motion_video_ids.txt"
    if ban_file.exists():
        for line in ban_file.read_text(encoding="utf-8").splitlines():
            video_id = line.strip()
            if video_id and not video_id.startswith("#"):
                banned.add(video_id)
    return banned


def product_id_from_url(url: str) -> str:
    m = re.search(r"/view/product/(\d+)", url or "")
    return m.group(1) if m else ""


def banned_product_ids(state: dict | None = None) -> set[str]:
    banned = set((state or {}).get("banned_product_ids") or [])
    ban_file = DATA_DIR / "banned_product_ids.txt"
    if ban_file.exists():
        for line in ban_file.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            banned.add(product_id_from_url(value) or value)
    return banned


def recent_product_ids_from_runs(limit: int) -> set[str]:
    if limit <= 0 or not RUNS_CSV.exists():
        return set()
    try:
        with RUNS_CSV.open(newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return set()
    ids = []
    for row in rows:
        product_id = product_id_from_url(row.get("product_url") or "")
        if product_id:
            ids.append(product_id)
    return set(ids[-limit:])


def reserved_product_ids(state: dict | None = None) -> set[str]:
    """Product IDs already reserved recently or by in-flight prepared jobs.

    TikTok product videos can point at the same TikTok Shop product. The video
    picker avoids recent video IDs, but concurrent workers need product-level
    reservation too, otherwise W4/W5/etc can pick different videos for the same
    product before any one job completes.
    """
    state = state or {}
    avoid_count = int(os.environ.get("PRODUCT_AVOID_COUNT", "120"))
    reserved = set(state.get("recent_product_ids", [])[-avoid_count:])
    reserved.update(recent_product_ids_from_runs(avoid_count))
    for info in (state.get("prepared_jobs") or {}).values():
        row = (info or {}).get("row") or {}
        product_id = (info or {}).get("product_id") or product_id_from_url(row.get("product_url") or "")
        if product_id:
            reserved.add(product_id)
    return reserved


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
    recent.update(banned_motion_video_ids(state))

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
    banned_products = banned_product_ids(state)
    reserved_products = reserved_product_ids(state)
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
        if cached and cached.get("product_image_url") and cached.get("product_image_urls"):
            product_url = cached.get("product_url", "")
            product_title = cached.get("product_title", "")
            product_image_urls = cached.get("product_image_urls") or [cached.get("product_image_url", "")]
        else:
            # Re-parse older cache entries too: Tokopedia PDPs often show a captcha,
            # but the TikTok video HTML usually embeds the product card image.
            tiktok_url = tiktok_video_url(entry, entry.get("_profile_url"))
            product_url, product_title, product_image_url, product_image_urls = extract_product_from_html_with_images(tiktok_url, limit=2)
            product_cache[video_id] = {
                "product_url": product_url,
                "product_title": product_title,
                "product_image_url": product_image_url,
                "product_image_urls": product_image_urls,
                "checked_at": iso(now_utc()),
            }
            dirty = True
        product_id = product_id_from_url(product_url)
        if product_id and product_id in banned_products:
            continue
        if product_id and product_id in reserved_products:
            continue
        if product_url or product_title:
            if dirty:
                state_save(state)
            return entry, product_url, product_title, product_image_urls
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


def extract_product_from_html_with_images(tiktok_url: str, limit: int = 2):
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    }
    try:
        text = requests.get(tiktok_url, headers=headers, timeout=45).text
    except Exception:
        return "", "", "", []
    # Product info often appears as escaped JSON inside the HTML.
    idx = text.find("product_id")
    if idx == -1:
        return "", "", "", []
    window = text[max(0, idx - 10000): idx + 30000]
    # Repeated unescape helps nested JSON strings.
    for _ in range(3):
        window = window.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        window = urllib.parse.unquote(window)
    title = ""
    product_id = ""
    seo_url = ""
    image_urls = oec_images_from_text(window, limit=limit)
    image_url = image_urls[0] if image_urls else ""
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
    return seo_url, title, image_url, image_urls


def extract_product_from_html(tiktok_url: str):
    seo_url, title, image_url, _ = extract_product_from_html_with_images(tiktok_url, limit=1)
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


def oec_images_from_text(text: str, limit: int = 2):
    # Prefer the product's ordered image list. TikTok's product card often has
    # full `cover_url`/`img_url` fields later in the JSON; those can correspond
    # to a card cover rather than the PDP's first gallery image.
    images = []
    seen = set()

    def add(url: str):
        if not url or url in seen:
            return
        seen.add(url)
        images.append(url)

    for m in re.finditer(r'"img"\s*:\s*\[(.*?)\]', text, re.S):
        for uri in re.findall(r'"(tos-[^"]+)"', m.group(1)):
            image_url = oec_uri_to_image_url(uri)
            if image_url:
                add(image_url)
                if len(images) >= limit:
                    return images

    urls = re.findall(r'https?://[^"\'\<\>\\\\\\s]+', text)
    for u in urls:
        u = urllib.parse.unquote(u.replace("&amp;", "&").replace("\\/", "/"))
        if any(host in u for host in ["p16-oec", "p19-oec", "ibyteimg"]):
            if any(ext in u for ext in ["webp", "jpeg", "jpg", "png"]):
                add(u)
                if len(images) >= limit:
                    return images
    return images


def first_oec_image_from_text(text: str):
    images = oec_images_from_text(text, limit=1)
    return images[0] if images else ""


def get_product_images(product_url: str, limit: int = 2):
    """Return up to `limit` product gallery image URLs from TikTok Shop/Tokopedia PDP metadata."""
    if not product_url:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    }
    try:
        text = requests.get(product_url, headers=headers, timeout=45).text
    except Exception:
        return []

    images = []
    seen = set()

    def add(url: str):
        if not url or url in seen:
            return
        seen.add(url)
        images.append(url)

    for image_url in oec_images_from_text(text, limit=limit):
        add(image_url)
        if len(images) >= limit:
            return images

    m = re.search(r'property="og:image"\s+content="([^"]+)"', text)
    if m:
        add(urllib.parse.unquote(m.group(1).replace("&amp;", "&")))
    return images[:limit]


def get_first_product_image(product_url: str):
    """Return first product image URL from TikTok Shop/Tokopedia PDP metadata."""
    images = get_product_images(product_url, limit=1)
    return images[0] if images else ""


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
