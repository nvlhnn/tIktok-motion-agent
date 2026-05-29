"""Tests for pipeline.validation."""

import os
import tempfile
from pathlib import Path

from PIL import Image

from pipeline.validation import validate_generated_reference_image


def _create_test_image(path: Path, width: int, height: int, solid=False, color=(255, 255, 255)):
    """Create a test JPEG image with a gradient to avoid blank detection."""
    img = Image.new("RGB", (width, height))
    if solid:
        img = Image.new("RGB", (width, height), color)
    else:
        # Create a gradient so it doesn't trigger blank/solid-color checks
        pixels = img.load()
        for y in range(height):
            for x in range(min(width, 64)):  # Only fill enough for the 64x64 resize check
                r = int(255 * x / max(width - 1, 1))
                g = int(255 * y / max(height - 1, 1))
                b = 128
                pixels[x, y] = (r, g, b)
    img.save(str(path), "JPEG", quality=85)


def test_validate_valid_image(tmp_path):
    img_path = tmp_path / "valid.jpg"
    _create_test_image(img_path, 2160, 3840)
    result = validate_generated_reference_image(img_path)
    assert result["ok"] is True
    assert result["width"] == 2160
    assert result["height"] == 3840
    assert result["aspect"] == "9:16"


def test_validate_minimum_size(tmp_path):
    img_path = tmp_path / "min.jpg"
    _create_test_image(img_path, 1080, 1920)
    result = validate_generated_reference_image(img_path)
    assert result["ok"] is True


def test_validate_rejects_too_small(tmp_path):
    img_path = tmp_path / "small.jpg"
    _create_test_image(img_path, 540, 960)
    try:
        validate_generated_reference_image(img_path)
        assert False, "Should have raised for too-small image"
    except RuntimeError as e:
        assert "too small" in str(e).lower()


def test_validate_rejects_wrong_ratio(tmp_path):
    img_path = tmp_path / "wrong_ratio.jpg"
    _create_test_image(img_path, 1920, 1080)  # 16:9 instead of 9:16
    try:
        validate_generated_reference_image(img_path)
        assert False, "Should have raised for wrong ratio"
    except RuntimeError as e:
        assert "too small" in str(e).lower() or "9:16" in str(e)


def test_validate_rejects_missing_file():
    try:
        validate_generated_reference_image(Path("/nonexistent/image.jpg"))
        assert False, "Should have raised for missing file"
    except RuntimeError as e:
        assert "not found" in str(e).lower()


def test_validate_rejects_blank_image(tmp_path):
    img_path = tmp_path / "blank.jpg"
    # Pure white solid image — should trigger the blank/near-solid check
    _create_test_image(img_path, 2160, 3840, solid=True, color=(255, 255, 255))
    try:
        validate_generated_reference_image(img_path)
        assert False, "Should have raised for blank image"
    except RuntimeError as e:
        assert "blank" in str(e).lower()


def test_validate_png_format(tmp_path):
    img_path = tmp_path / "valid.png"
    img = Image.new("RGB", (2160, 3840))
    pixels = img.load()
    for y in range(img.height):
        for x in range(min(img.width, 64)):
            pixels[x, y] = (int(255 * x / max(img.width - 1, 1)), int(255 * y / max(img.height - 1, 1)), 128)
    img.save(str(img_path), "PNG")
    result = validate_generated_reference_image(img_path)
    assert result["ok"] is True
    assert result["format"] == "PNG"
