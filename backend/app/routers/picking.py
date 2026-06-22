"""Barcode/EAN order picking — the handscanner counterpart to vision receiving.

Where ``/receiving`` identifies boxes by photo + AI, this books an order line by
a scanned EAN: one scan = one unit. It deliberately shares the vision flow's
machinery rather than forking a second model:

- the same write path (``services.booking.apply_booking``), so stock and order
  status stay consistent with vision bookings;
- the same per-order module guard (``assert_order_module``), keyed on the
  *order's* organization — couriers have no org of their own.

Gated on the order's ``barcode_picking`` module. Scoped to the selected order
(the courier picks one order at a time), unlike the week-wide vision scope.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import assert_order_module, require_inbound_booker
from app.database import get_db
from app.events import publish_event
from app.models import Order, OrderLine, SKU, User
from app.schemas import EanScanRequest, EanScanResponse
from app.services.booking import apply_booking

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/picking", tags=["picking"], dependencies=[Depends(require_inbound_booker)]
)


@router.post("/scan-ean", response_model=EanScanResponse)
def scan_ean(
    body: EanScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Book one unit on a barcode order by its scanned EAN.

    Looks the EAN up within the order's organization (EAN is unique per org),
    finds the matching open order line, and books a single unit through the
    shared booking service.
    """
    order = db.get(Order, body.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    # Owners/members may only act on their own organization's orders; couriers
    # and platform admins serve across organizations. assert_order_module checks
    # the order's *module*, not the caller's *access* — so this guard is what
    # keeps an owner of org A out of org B's order. Same check as receiving.
    if (
        user.role in ("owner", "member")
        and order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")
    assert_order_module(order, "barcode_picking", user)

    ean = body.ean.strip()
    if not ean:
        raise HTTPException(400, "Geen EAN gescand")

    # EAN is unique per organization (uq_skus_org_ean); resolve within the
    # order's org so two merchants stocking the same EAN never cross.
    sku = (
        db.query(SKU)
        .filter(SKU.organization_id == order.organization_id, SKU.ean == ean)
        .first()
    )
    if not sku:
        raise HTTPException(404, f"Geen product met EAN {ean} in deze organisatie")

    line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == order.id,
            OrderLine.sku_id == sku.id,
            OrderLine.booked_count < OrderLine.quantity,
        )
        .first()
    )
    if not line:
        # Distinguish "not on this order" from "already complete" so the courier
        # knows whether they grabbed the wrong box or simply finished it.
        on_order = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order.id, OrderLine.sku_id == sku.id)
            .first()
        )
        if on_order:
            raise HTTPException(409, f"{sku.name} is al volledig geboekt op deze order")
        raise HTTPException(400, f"{sku.name} staat niet op deze order")

    result = apply_booking(
        db,
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku.id,
        quantity=1,
        cap_remaining=None,
        scanned_by=user.id,
        scan_image_path=None,
        confidence=None,
    )

    rolcontainer = f"KLANT {line.customer_name.upper()}"
    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "ean": ean,
            "rolcontainer": rolcontainer,
            "klant": line.customer_name,
            "order_completed": result.order_completed,
            "quantity": result.booked_quantity,
            "pick_method": "barcode",
        },
        user=user,
        resource_type="booking",
        resource_id=result.last_booking_id,
    )

    return EanScanResponse(
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=line.customer_name,
        rolcontainer=rolcontainer,
        booked_quantity=result.booked_quantity,
        remaining_quantity=result.remaining,
        order_completed=result.order_completed,
    )
