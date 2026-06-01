"""Tests for upload image normalization (HEIC support + transcoding to JPEG)."""

import io

import pytest
from PIL import Image

from app.services.embedding import UnsupportedImageError, normalize_upload_to_jpeg


def _encode(fmt: str, mode: str = "RGB", size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (10, 20, 30, 40)[: len(mode)] or (10, 20, 30)).save(buf, format=fmt)
    return buf.getvalue()


def test_heic_is_transcoded_to_jpeg():
    """iPhone HEIC uploads decode and re-encode to a real JPEG (the bug we hit)."""
    out = normalize_upload_to_jpeg(_encode("HEIF"))
    assert out[:3] == b"\xff\xd8\xff"  # JPEG magic
    assert Image.open(io.BytesIO(out)).size == (40, 30)


def test_png_with_alpha_becomes_jpeg():
    out = normalize_upload_to_jpeg(_encode("PNG", mode="RGBA"))
    assert out[:3] == b"\xff\xd8\xff"
    assert Image.open(io.BytesIO(out)).mode in ("RGB", "L")


def test_jpeg_passthrough_stays_decodable():
    out = normalize_upload_to_jpeg(_encode("JPEG"))
    assert Image.open(io.BytesIO(out)).size == (40, 30)


def test_garbage_bytes_raise_unsupported():
    with pytest.raises(UnsupportedImageError):
        normalize_upload_to_jpeg(b"this is definitely not an image")


def test_large_image_is_downscaled_to_cap():
    """Oversized uploads are downscaled so the stored JPEG stays bounded."""
    buf = io.BytesIO()
    Image.new("RGB", (5000, 3000), (200, 100, 50)).save(buf, format="JPEG")
    out = normalize_upload_to_jpeg(buf.getvalue(), max_dimension=2048)
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) == 2048
    assert (w, h) == (2048, 1228)  # aspect ratio preserved
