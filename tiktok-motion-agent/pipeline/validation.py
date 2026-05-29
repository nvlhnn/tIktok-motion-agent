import os
from pathlib import Path

from PIL import Image, ImageStat

from .config import int_env


def validate_generated_reference_image(ref_path: Path) -> dict:
    """Validate the generated try-on input image before video submission.

    This is a hard, local gate for objective checks. The agent should still do a
    visual/product/modesty review before calling complete.
    """
    ref_path = Path(ref_path).expanduser().resolve()
    if not ref_path.exists():
        raise RuntimeError(f"Generated reference image not found: {ref_path}")
    if not ref_path.is_file():
        raise RuntimeError(f"Generated reference path is not a file: {ref_path}")
    if ref_path.stat().st_size <= 0:
        raise RuntimeError(f"Generated reference image is empty: {ref_path}")

    min_width = int_env("GENERATED_REFERENCE_MIN_WIDTH", 1080)
    min_height = int_env("GENERATED_REFERENCE_MIN_HEIGHT", 1920)
    allowed_formats = {x.strip().upper() for x in os.environ.get("GENERATED_REFERENCE_FORMATS", "JPEG,JPG,PNG,WEBP").split(",") if x.strip()}
    max_size_mb = float(os.environ.get("GENERATED_REFERENCE_MAX_SIZE_MB", "25"))
    max_size_bytes = int(max_size_mb * 1024 * 1024)
    if ref_path.stat().st_size > max_size_bytes:
        raise RuntimeError(f"Generated reference image too large: {ref_path.stat().st_size} bytes > {max_size_bytes} bytes")

    try:
        with Image.open(ref_path) as img:
            img.verify()
        with Image.open(ref_path) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
            mode = img.mode
            stat_img = img.convert("RGB").resize((64, 64))
            extrema = ImageStat.Stat(stat_img).extrema
    except Exception as e:
        raise RuntimeError(f"Generated reference image is unreadable/corrupt: {e}") from e

    if fmt == "JPG":
        fmt = "JPEG"
    normalized_allowed = {"JPEG" if f == "JPG" else f for f in allowed_formats}
    if normalized_allowed and fmt not in normalized_allowed:
        raise RuntimeError(f"Generated reference format {fmt or 'unknown'} not allowed; allowed={sorted(normalized_allowed)}")
    if width < min_width or height < min_height:
        raise RuntimeError(f"Generated reference too small: {width}x{height}; minimum is {min_width}x{min_height}")
    if width * 16 != height * 9:
        raise RuntimeError(f"Generated reference must be exact 9:16, got {width}x{height}")
    if all((hi - lo) < 3 for lo, hi in extrema):
        raise RuntimeError("Generated reference appears blank/near-solid color")

    return {
        "ok": True,
        "path": str(ref_path),
        "format": fmt,
        "mode": mode,
        "width": width,
        "height": height,
        "aspect": "9:16",
        "size_bytes": ref_path.stat().st_size,
    }
