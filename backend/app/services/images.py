"""Shared image upload helpers for receiving and vision routers."""

import os
import uuid

from fastapi import HTTPException, UploadFile

from app.config import settings

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


def read_image(file: UploadFile) -> bytes:
    """Read uploaded image bytes and reject files larger than 10 MB."""
    image_bytes = file.file.read()
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")
    return image_bytes


def save_scan_image(image_bytes: bytes) -> str:
    """Save scan image to disk and return the file path."""
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)
    return scan_path
