"""SKU active-status auto-management for opted-in organizations.

For organizations with auto_inactivate_no_images=True, SKU.active is a
derived signal: the SKU is active iff its product fields are complete, OR it
has at least one reference image that has finished processing successfully
(or is still being processed under the optimistic-flip rule). For other
organizations the active flag is left under manual control.

"Complete" means the merchant has filled in everything needed to sell the
product: a real name plus, for wines, all required wine attributes. A
finished concept therefore goes active without waiting for a photo — the
courier can capture one at scan time. An existing image still keeps a SKU
active so nothing that is live today drops out.

This module is the single source of truth for that recomputation. Call
recompute_active() after any change that could affect the answer:
  - SKU creation
  - SKU field/attribute update (name, category, wine attributes)
  - Reference image upload
  - Reference image processing transition (done / failed)
  - Reference image deletion
"""
from sqlalchemy.orm import Session

from app.models import ReferenceImage, SKU, Organization
from app.schemas import WINE_ATTRIBUTE_KEYS


# Image processing states that count as "the SKU has a usable image".
# Optimistic flip: pending/processing count until proven failed.
_USABLE_STATUSES = ("pending", "processing", "done")


def org_auto_inactivates_without_images(org: Organization | None) -> bool:
    """Whether the rule applies to a given organization."""
    return bool(org and org.auto_inactivate_no_images)


def is_complete(sku: SKU) -> bool:
    """Whether a SKU has all the fields needed to be sold/picked.

    Wines need every required wine attribute — inbound concepts are unfinished
    wines, so a bare name is not enough; the merchant must fill the wine fields.
    Non-wine products need a real name that is not the auto-generated
    ``Concept <code>`` placeholder.
    """
    name = (sku.name or "").strip()
    if not name or name == f"Concept {sku.sku_code}":
        return False

    if sku.category == "wine":
        attrs = sku.attributes_dict
        return all((attrs.get(k) or "").strip() for k in WINE_ATTRIBUTE_KEYS)

    # Non-wine: a real name is enough to consider it sellable.
    return True


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

    desired = is_complete(sku) or _has_usable_image(sku, db)
    if sku.active != desired:
        sku.active = desired
        db.add(sku)
