"""Stock movement & inventory balance mutation.

Single source of truth for adjusting on-hand stock. Used by the inventory
router (manual counts/adjustments, shipment booking) and the receiving router
(picking decrements bookings against stock). Lives in a service so neither
router has to import the other.
"""
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import SKU, InventoryBalance, StockMovement


def apply_stock_movement(
    db: Session,
    *,
    sku_id: int,
    organization_id: int,
    quantity: int,
    movement_type: str,
    reference_type: str | None = None,
    reference_id: int | None = None,
    note: str | None = None,
    performed_by: int,
) -> StockMovement:
    """Create a stock movement and update the inventory balance.

    Does NOT commit — the caller controls the transaction boundary.
    Raises HTTPException(409) if the resulting balance would go negative.
    """
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .with_for_update()
        .first()
    )
    if not balance:
        balance = InventoryBalance(
            sku_id=sku_id,
            organization_id=organization_id,
            quantity_on_hand=0,
        )
        db.add(balance)
        db.flush()

    new_qty = balance.quantity_on_hand + quantity
    if quantity < 0 and abs(quantity) > balance.quantity_available:
        sku = db.get(SKU, sku_id)
        sku_code = sku.sku_code if sku else str(sku_id)
        raise HTTPException(
            409,
            f"Onvoldoende voorraad voor {sku_code}: "
            f"{balance.quantity_available} beschikbaar, {abs(quantity)} nodig",
        )
    if new_qty < balance.quantity_reserved:
        raise HTTPException(
            409,
            "Voorraad kan niet onder het gereserveerde aantal komen",
        )

    balance.quantity_on_hand = new_qty
    balance.last_movement_at = func.now()

    movement = StockMovement(
        sku_id=sku_id,
        organization_id=organization_id,
        movement_type=movement_type,
        quantity=quantity,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        performed_by=performed_by,
    )
    db.add(movement)
    db.flush()
    return movement


def adjust_reservation(
    db: Session, *, sku_id: int, organization_id: int, delta: int
) -> None:
    """Adjust reserved stock by ``delta`` (no physical movement, no movement row).

    Reservation is how the available number an external channel sees already
    excludes open orders: a live channel order reserves at activation and
    releases as it is picked, so ``available`` stays stable across the
    sale → pick window (no oversell). Clamped at 0 so a release on a product
    that never reserved (e.g. vision picks) is a harmless no-op.

    Does NOT commit — the caller owns the transaction boundary.
    """
    if delta == 0:
        return
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .with_for_update()
        .first()
    )
    if not balance:
        if delta < 0:
            return  # nothing reserved to release
        balance = InventoryBalance(
            sku_id=sku_id, organization_id=organization_id, quantity_on_hand=0
        )
        db.add(balance)
        db.flush()
    balance.quantity_reserved = max(0, balance.quantity_reserved + delta)
