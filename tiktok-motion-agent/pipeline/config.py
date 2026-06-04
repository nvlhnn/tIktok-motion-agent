import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOWNLOADS_DIR = ROOT / "downloads"
REVIEWS_DIR = ROOT / "reviews"
LOGS_DIR = ROOT / "logs"
STATE_PATH = DATA_DIR / "state.json"
RUNS_CSV = DATA_DIR / "runs.csv"
TIKTOK_LIST_CACHE_PATH = DATA_DIR / "tiktok_entries_cache.json"

MODEST_TRYON_PROMPT = (
    "Preserve master face/identity, lighting, wooden-door background, camera distance, and mid-thigh-up framing. "
    "Use the product TikTok video frame references as the primary outfit source, not the product-card/PDP stills. "
    "Copy the outfit worn in the product TikTok video frames VERY closely. Preserve the exact garment construction, not just the general style: garment category, color/tone, pattern/print, fabric texture, neckline/collar, sleeve shape and cuffs, front/back closures, seams, waist construction, hem shape, trims, buttons, lace, ruffles, pleats, ties, pockets, panels, layering, and visible set composition. "
    "Use product-card/PDP stills only as secondary clarification if video frames are ambiguous. Distinctive product details from the TikTok video must be clearly visible and structurally accurate; do not simplify, smooth out, hide, replace, or reinterpret them. "
    "Avoid generic fashion interpretation; do not convert the TikTok outfit into a similar-looking but different item. Do not hide important neckline, closure, waist, sleeve, hem, print, or trim details under hijab, pose, arm placement, crop, bag, or accessories. "
    "The person must not wear, carry, hold, sling, or pose with any bag, purse, handbag, tote, backpack, clutch, crossbody bag, shoulder bag, or strap; no bag-like accessory anywhere in the image. "
    "Restyle top, bottom, hijab, and accessories to match the product TikTok outfit while keeping modest coverage; if the TikTok outfit shows a bottom, copy it, otherwise use a modest matching bottom. "
    "Do not keep original jeans, cream hijab, or bag by default. No UI/text/watermark/product model/background. Realistic fit, true TikTok vertical 9:16 composition. "
    "Keep the subject close to camera like the master reference; do not zoom out, do not generate head-to-toe/full-body framing, do not show shoes or extra floor space."
)

DEFAULT_PROMPT = (
    "Transfer the body movement and camera rhythm from the TikTok reference video to the person in the reference image. "
    "Preserve identity, pose style, and background as much as possible while enforcing modest Muslim-friendly styling. "
    "Keep hijab/head covering intact and keep the outfit fully modest throughout all frames. The hijab/underscarf must cover the entire neck, collarbone, and upper chest in every frame. "
    "ABSOLUTE REQUIREMENTS: zero visible neck skin, zero visible collarbone, zero visible upper chest, no open neckline, no V-neck/open collar gap, no cleavage, no exposed chest, no shorts, no bare legs, no exposed thighs, no bare shoulders, no bare upper arms, no exposed waist/back, no transparent/sheer clothing, no tight body-revealing fit. "
    "Do not alter the affiliated product itself: keep its original cut, sleeve length, neckline, silhouette, color, pattern, texture, and style as accurately as possible. "
    "If any source product is sleeveless, short, low-cut, open-collar, V-neck, cropped, sheer, tight, or revealing, keep the product unchanged and make the full outfit modest by layering separate clothing under or around it: matching long-sleeve inner shirt, high-neck turtleneck/dickey inner layer, neck-covering underscarf, leggings/full-length pants, long skirt, and/or outer cardigan/blazer. "
    "For button-up shirts, close the collar area visually with a high-neck inner layer or scarf so no skin is visible below the chin. "
    "The product must look like the same affiliated item; modest coverage should come from added inner/outer layers, not by modifying the product design. "
    "Keep the motion natural, realistic, vertical social-video style, with stable identity and clean lighting. "
    "Keep the final video in TikTok vertical 9:16 composition matching the motion reference, but do not crop, cut off, zoom in, or remove any important body parts, head, hijab, clothing, product details, hands, or feet. Preserve full subject framing safely within the vertical frame."
)

COLUMNS = [
    "created_at",
    "job_id",
    "status",
    "product_video_url",
    "product_url",
    "product_title",
    "product_image_url",
    "input_image_url",
    "motion_tiktok_video_url",
    "motion_supabase_video_url",
    "result_supabase_url",
    "caption",
    "provider",
    "provider_auth_label",
    "provider_task_id",
    "provider_result_url",
    "delete_after",
    "error",
    "uploaded_at",
    "scheduled_at",
    "buffer_post_id",
    "buffer_status",
    "buffer_error",
    "external_link",
    "tiktok_post_url",
    "facebook_post_url",
    "instagram_post_url",
    "post_external_links",
    "buffer_channel_id",
    "uploaded_via",
    "upload_attempts",
    "tiktok_views",
    "tiktok_likes",
    "tiktok_comments",
    "tiktok_shares",
    "stats_checked_at",
    "product_match_status",
    "product_match_score",
    "product_match_reason",
    "product_match_checked_at",
    "action_needed",
    "input_image_validation",
    "product_image_url_2",
]

STATUS_VALUES = [
    "STARTED",
    "NEEDS_REFERENCE_IMAGE",
    "QUEUED",
    "SUBMITTED",
    "PROCESSING",
    "COMPLETED",
    "READY_TO_UPLOAD",
    "SCHEDULED_UPLOAD",
    "UPLOADING",
    "REJECTED",
    "UPLOADED",
    "UPLOAD_FAILED",
    "READY_TO_AFFILIATE",
    "AFFILIATED",
    "FAILED",
    "TIMEOUT",
]


TERMINAL_STATUSES = {"COMPLETED", "READY_TO_UPLOAD", "SCHEDULED_UPLOAD", "REJECTED", "UPLOADED", "UPLOAD_FAILED", "READY_TO_AFFILIATE", "AFFILIATED", "FAILED", "TIMEOUT"}
# Statuses that still represent in-flight local generation/provider work and
# should block starting another generation. Once Buffer accepts a post
# (SCHEDULED_UPLOAD), Buffer owns the scheduled publishing and generation may
# continue with the next video.
ACTIVE_STATUSES = {"QUEUED", "SUBMITTED", "PROCESSING", "UPLOADING"}

STATUS_COLORS = {
    # Intentionally high-contrast and unique per status.
    "STARTED": {"backgroundColor": {"red": 0.80, "green": 0.86, "blue": 1.00}},          # blue
    "NEEDS_REFERENCE_IMAGE": {"backgroundColor": {"red": 1.00, "green": 0.93, "blue": 0.55}}, # yellow
    "QUEUED": {"backgroundColor": {"red": 0.86, "green": 0.80, "blue": 1.00}},          # lavender
    "SUBMITTED": {"backgroundColor": {"red": 0.74, "green": 0.82, "blue": 1.00}},       # periwinkle
    "PROCESSING": {"backgroundColor": {"red": 0.65, "green": 0.92, "blue": 1.00}},       # cyan
    "COMPLETED": {"backgroundColor": {"red": 0.70, "green": 0.92, "blue": 0.70}},        # green
    "READY_TO_UPLOAD": {"backgroundColor": {"red": 0.58, "green": 1.00, "blue": 0.78}},  # mint
    "SCHEDULED_UPLOAD": {"backgroundColor": {"red": 0.76, "green": 0.88, "blue": 1.00}}, # light blue
    "UPLOADING": {"backgroundColor": {"red": 0.65, "green": 0.92, "blue": 1.00}},        # cyan
    "REJECTED": {"backgroundColor": {"red": 1.00, "green": 0.60, "blue": 0.60}},         # red
    "UPLOADED": {"backgroundColor": {"red": 0.55, "green": 0.78, "blue": 1.00}},         # stronger blue
    "UPLOAD_FAILED": {"backgroundColor": {"red": 1.00, "green": 0.62, "blue": 0.62}},    # red
    "READY_TO_AFFILIATE": {"backgroundColor": {"red": 1.00, "green": 0.86, "blue": 0.45}}, # gold
    "AFFILIATED": {"backgroundColor": {"red": 0.84, "green": 0.67, "blue": 1.00}},       # purple
    "FAILED": {"backgroundColor": {"red": 1.00, "green": 0.62, "blue": 0.62}},           # red
    "TIMEOUT": {"backgroundColor": {"red": 1.00, "green": 0.73, "blue": 0.45}},          # orange
}


def load_env(path: Path = ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env: {name}")
    return v


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Invalid integer env {name}={raw!r}")


def truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
