"""Backfill SKU.active based on reference-image presence for opted-in orgs.

For every Organization with auto_inactivate_no_images=True, set each of its
SKUs' active flag to True iff the SKU has at least one reference image with
processing_status in (pending, processing, done) — i.e. at least one usable
image under the optimistic-flip rule.

Idempotent: safe to re-run. Prints a summary of what changed.

Usage (from repository root, with backend deps installed):
    python -m scripts.backfill_active_from_images
"""
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import SKU, Organization
from app.services.product_status import recompute_active


def main() -> None:
    db = SessionLocal()
    try:
        orgs = (
            db.query(Organization)
            .filter(Organization.auto_inactivate_no_images.is_(True))
            .all()
        )
        if not orgs:
            print("No organizations have auto_inactivate_no_images enabled.")
            return

        total_changed = 0
        for org in orgs:
            skus = (
                db.query(SKU)
                .options(selectinload(SKU.reference_images))
                .filter(SKU.organization_id == org.id)
                .all()
            )
            org_changed = 0
            for sku in skus:
                before = sku.active
                recompute_active(sku, db)
                if sku.active != before:
                    org_changed += 1
            db.commit()
            print(
                f"[{org.slug}] processed {len(skus)} SKUs, flipped {org_changed}."
            )
            total_changed += org_changed

        print(f"Done. Total SKUs flipped: {total_changed}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
