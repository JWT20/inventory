"""Read-only view on the stock the advice app is holding.

The reservation endpoints in ``integrations`` are machine-to-machine: wijnadvies1
reserves, collects and releases with an API key. Nothing showed those holds to
the person whose shelf they sit on — inventory reported three bottles reserved
with nothing to say which order held them.

Deliberately read-only. Lifting a hold from this side would leave wijnadvies1
believing the order is still reserved, so the correct place to cancel an order
remains the advice app itself.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.auth import require_merchant
from app.database import get_db
from app.models import AdviceReservation, User
from app.schemas import AdviceReservationAdminItem, AdviceReservationAdminLine
from app.services.advice_channel import resolve_advice_organization

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advice-reservations", tags=["advice-reservations"])


def _to_item(reservation: AdviceReservation) -> AdviceReservationAdminItem:
    lines = [
        AdviceReservationAdminLine(
            sku_id=line.sku_id,
            sku_code=line.sku.sku_code,
            sku_name=line.sku.name,
            source_product_id=line.sku.source_product_id,
            quantity=line.quantity,
        )
        for line in sorted(reservation.lines, key=lambda item: item.sku.sku_code)
    ]
    return AdviceReservationAdminItem(
        id=reservation.id,
        external_order_id=reservation.external_order_id,
        order_reference=reservation.order_reference,
        fulfillment_method=reservation.fulfillment_method,
        inventory_location=reservation.inventory_location,
        status=reservation.status,
        created_at=reservation.created_at,
        collected_at=reservation.collected_at,
        released_at=reservation.released_at,
        total_quantity=sum(line.quantity for line in lines),
        lines=lines,
    )


@router.get("", response_model=list[AdviceReservationAdminItem])
def list_advice_reservations(
    organization_id: int | None = None,
    status: str | None = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
):
    """List advice-app holds, newest first; active ones by default."""
    org_id = resolve_advice_organization(db, user, organization_id)

    query = (
        db.query(AdviceReservation)
        .options(selectinload(AdviceReservation.lines))
        .filter(AdviceReservation.organization_id == org_id)
    )
    if status and status != "all":
        if status not in ("active", "collected", "released"):
            raise HTTPException(400, "Onbekende status")
        query = query.filter(AdviceReservation.status == status)

    reservations = (
        query.order_by(AdviceReservation.created_at.desc(), AdviceReservation.id.desc())
        .limit(limit)
        .all()
    )
    return [_to_item(r) for r in reservations]
