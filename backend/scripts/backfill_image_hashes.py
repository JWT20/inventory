"""Backfill image_hash for existing reference images.

Usage:
    python -m scripts.backfill_image_hashes
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import ReferenceImage
from app.routers.skus import compute_image_hash


def main() -> None:
    db = SessionLocal()
    try:
        images = db.query(ReferenceImage).filter(ReferenceImage.image_hash.is_(None)).all()
        print(f"Found {len(images)} images without hash")
        updated = 0
        for img in images:
            if not os.path.exists(img.image_path):
                print(f"  SKIP image {img.id}: file not found at {img.image_path}")
                continue
            with open(img.image_path, "rb") as f:
                image_bytes = f.read()
            img.image_hash = compute_image_hash(image_bytes)
            updated += 1
            if updated % 50 == 0:
                db.commit()
                print(f"  Updated {updated} images...")
        db.commit()
        print(f"Done. Updated {updated}/{len(images)} images.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
