"""Read-only view on the advice app's delivery orders.

These orders exist here because picking and the shipping-label gate hang off an
``Order`` row, but they are only being observed: they are deliberately filtered
out of the order list, Scan & Boek and the week planning. Without a view of their
own they would be invisible to the merchant who has to get the parcels out, which
is the same gap the reservation view closed for pickups.

Read-only *on the order*, for the same reason as that view: the advice app owns
it. Changing what the customer bought from this side would leave the two systems
disagreeing, and only a customer at the door would find out. Registering the
order's boxes at the carrier is the one write here, and it touches Veloyd rather
than the order.

Not folded into the Shopify/bol reconciliation endpoints, though the row builder
there is generic enough. Those require a platform admin who names an
organization, while the advice organization is configured rather than chosen —
and the row would have to carry a delivery address that means nothing for a
channel whose parcels Veloyd's own webshop link already created.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.auth import require_merchant
from app.database import get_db
from app.models import ChannelSyncLog, Order, User
from app.schemas import (
    AdviceOrderAdminItem,
    AdviceOrderAdminLine,
    AdviceOrderParcel,
    DeliveryAddressResponse,
)
from app.services.advice_channel import ADVICE_CHANNEL, resolve_advice_organization
from app.services.advice_shipping import (
    SHIPPABLE_STATUSES,
    AdviceShippingError,
    create_parcels,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advice-orders", tags=["advice-orders"])


def _to_item(order: Order, unmatched: list[str]) -> AdviceOrderAdminItem:
    lines = [
        AdviceOrderAdminLine(
            sku_id=line.sku_id,
            sku_code=line.sku.sku_code,
            sku_name=line.sku.name,
            quantity=line.quantity,
        )
        for line in sorted(order.lines, key=lambda item: item.sku.sku_code)
    ]
    return AdviceOrderAdminItem(
        order_id=order.id,
        reference=order.reference,
        external_order_id=order.external_id,
        order_reference=order.channel_reference,
        status=order.status,
        ordered_at=order.ordered_at,
        created_at=order.created_at,
        total_quantity=sum(line.quantity for line in lines),
        delivery_address=(
            DeliveryAddressResponse.model_validate(order.delivery_address)
            if order.delivery_address
            else None
        ),
        lines=lines,
        unmatched_products=unmatched,
        parcels=[AdviceOrderParcel.model_validate(parcel) for parcel in order.parcels],
    )


@router.get("", response_model=list[AdviceOrderAdminItem])
def list_advice_orders(
    organization_id: int | None = None,
    status: str | None = Query(default="observed"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
):
    """List advice-app delivery orders, newest first; observed ones by default.

    ``status=all`` also returns orders that were promoted out of observing, so a
    later go-live does not make the view lie by quietly dropping them.
    """
    org_id = resolve_advice_organization(db, user, organization_id)

    query = (
        db.query(Order)
        .options(
            selectinload(Order.lines),
            selectinload(Order.delivery_address),
            selectinload(Order.parcels),
        )
        .filter(
            Order.organization_id == org_id,
            Order.channel == ADVICE_CHANNEL,
        )
    )
    if status and status != "all":
        if status not in ("observed", "active", "completed", "shipped", "closed"):
            raise HTTPException(400, "Onbekende status")
        query = query.filter(Order.status == status)

    orders = (
        query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit).all()
    )
    if not orders:
        return []

    # The unmatched products live on the sync log, not on the order: a product the
    # catalogue does not know has no line to hang off.
    unmatched_by_external: dict[str, list[str]] = {}
    external_ids = [order.external_id for order in orders if order.external_id]
    if external_ids:
        for log in (
            db.query(ChannelSyncLog)
            .filter(
                ChannelSyncLog.organization_id == org_id,
                ChannelSyncLog.channel == ADVICE_CHANNEL,
                ChannelSyncLog.external_id.in_(external_ids),
            )
            .all()
        ):
            try:
                unmatched_by_external[log.external_id] = json.loads(
                    log.unmatched_eans or "[]"
                )
            except ValueError:
                # A malformed log must not hide the order it belongs to.
                logger.warning(
                    "Onleesbare synclog voor advice-order %s", log.external_id
                )
                unmatched_by_external[log.external_id] = []

    return [
        _to_item(order, unmatched_by_external.get(order.external_id or "", []))
        for order in orders
    ]


@router.post("/{order_id}/parcels", response_model=list[AdviceOrderParcel])
def register_advice_parcels(
    order_id: int,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
):
    """Register this order's boxes at the carrier, or finish a partial attempt.

    The import already tries this on its own. This is the way back when Veloyd
    was unreachable at that moment, or when only part of a multi-box order was
    accepted: it asks for the boxes that are still missing and never for one
    that already exists.

    Write-only in the direction of the carrier — the order itself is not
    touched, because the advice app owns it.
    """
    org_id = resolve_advice_organization(db, user, organization_id)
    order = db.get(Order, order_id)
    if not order or order.organization_id != org_id or order.channel != ADVICE_CHANNEL:
        raise HTTPException(404, "Order niet gevonden")
    if order.status not in SHIPPABLE_STATUSES:
        # An observed order must reach nothing outside, an incomplete one would
        # ship short, and a shipped one is already gone. The service refuses
        # these too; this is only the friendlier status code.
        raise HTTPException(
            409, f"Order met status {order.status} wordt niet aangemeld"
        )

    try:
        parcels = create_parcels(db, order)
    except AdviceShippingError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [AdviceOrderParcel.model_validate(parcel) for parcel in parcels]
