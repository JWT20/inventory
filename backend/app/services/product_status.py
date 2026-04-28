"""SKU active-status auto-management for opted-in organizations.

For organizations with auto_inactivate_no_images=True, SKU.active is a
derived signal: the SKU is active iff it has at least one reference image
that has finished processing successfully (or is still being processed
under the optimistic-flip rule). For other organizations the active flag
is left under manual control.

This module is the single source of truth for that recomputation. Call
recompute_active() after any change that could affect the answer:
  - SKU creation
  - Reference image upload
  - Reference image processing transition (done / failed)
  - Reference image deletion
"""
from sqlalchemy.orm import Session

from app.models import ReferenceImage, SKU, Organization


# Image processing states that count as "the SKU has a usable image".
# Optimistic flip: pending/processing count until proven failed.
_USABLE_STATUSES = ("pending", "processing", "done")


def org_auto_inactivates_without_images(org: Organization | None) -> bool:
    """Whether the rule applies to a given organization."""
    return bool(org and org.auto_inactivate_no_images)


def _has_usable_image(sku: SKU, db: Session) -> bool:
    """Query directly so the answer is correct mid-transaction.

    The in-memory `sku.reference_images` collection is not guaranteed to
    reflect rows added or deleted in the current session before a refresh,
    so we ask the session itself.
    """
    return db.query(
        db.query(ReferenceImage)
        .filter(
            ReferenceImage.sku_id == sku.id,
            ReferenceImage.processing_status.in_(_USABLE_STATUSES),
        )
        .exists()
    ).scalar()


def recompute_active(sku: SKU, db: Session) -> None:
    """Recompute SKU.active for opted-in organizations.

    No-op for SKUs whose organization has not enabled the rule, so other
    tenants keep manual control of their active flag.
    """
    if not org_auto_inactivates_without_images(sku.organization):
        return

    desired = _has_usable_image(sku, db)
    if sku.active != desired:
        sku.active = desired
        db.add(sku)
