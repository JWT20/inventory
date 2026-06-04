"""Courier-facing endpoints: per-month box scanning earnings / invoicing.

A courier invoices the customer a fixed amount per scanned box (1 Booking =
1 box). This router aggregates the bookings a courier scanned within a given
month, grouped per customer, so the courier sees exactly what to invoice.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_warehouse
from app.database import get_db
from app.models import Booking, CourierBillingRate, Customer, Order, OrderLine, User
from app.schemas import CourierEarningsCustomer, CourierEarningsResponse

router = APIRouter(prefix="/courier", tags=["courier"])

# Only finalised orders are invoiceable: an order is billed once it is fully
# picked (completed) or deliberately wrapped up (closed). Still-open orders
# (draft/pending_images/active) and cancelled ones are excluded.
BILLABLE_ORDER_STATUSES = ("completed", "closed")


def _month_bounds(month: str | None) -> tuple[datetime.datetime, datetime.datetime, str]:
    """Resolve a 'YYYY-MM' string to [start, next_month_start) datetimes.

    Defaults to the current month when not provided.
    """
    if month:
        try:
            year, mon = (int(p) for p in month.split("-", 1))
            start = datetime.datetime(year, mon, 1)
        except (ValueError, TypeError):
            raise HTTPException(422, "Ongeldige maand, gebruik formaat YYYY-MM")
    else:
        today = datetime.date.today()
        start = datetime.datetime(today.year, today.month, 1)

    if start.month == 12:
        end = datetime.datetime(start.year + 1, 1, 1)
    else:
        end = datetime.datetime(start.year, start.month + 1, 1)
    return start, end, f"{start.year:04d}-{start.month:02d}"


def _get_rate(db: Session) -> CourierBillingRate:
    rate = db.get(CourierBillingRate, 1)
    if rate is None:
        # Fall back to defaults if the seed row is missing.
        rate = CourierBillingRate(id=1, charge_cents=50, platform_cents=17, courier_cents=33)
    return rate


@router.get("/earnings", response_model=CourierEarningsResponse)
def courier_earnings(
    month: str | None = Query(None, description="Maand als YYYY-MM, standaard huidige maand"),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Boxes the current courier scanned in a month, grouped per customer."""
    start, end, month_label = _month_bounds(month)
    rate = _get_rate(db)

    rows = (
        db.query(
            OrderLine.customer_id,
            Customer.name,
            OrderLine.klant,
            func.count(Booking.id),
        )
        .join(OrderLine, Booking.order_line_id == OrderLine.id)
        .join(Order, Booking.order_id == Order.id)
        .outerjoin(Customer, OrderLine.customer_id == Customer.id)
        .filter(
            Booking.scanned_by == user.id,
            Booking.created_at >= start,
            Booking.created_at < end,
            Order.status.in_(BILLABLE_ORDER_STATUSES),
        )
        .group_by(OrderLine.customer_id, Customer.name, OrderLine.klant)
        .all()
    )

    # Merge by resolved customer name (a null customer_id falls back to the
    # free-text 'klant' field; multiple raw rows may resolve to one name).
    per_customer: dict[str, int] = {}
    for customer_id, customer_name, klant, count in rows:
        name = customer_name or klant or "Onbekend"
        per_customer[name] = per_customer.get(name, 0) + int(count)

    total_boxes = sum(per_customer.values())

    customers = [
        CourierEarningsCustomer(
            customer_name=name,
            boxes=boxes,
            charge_amount=round(boxes * rate.charge_cents / 100, 2),
        )
        for name, boxes in sorted(
            per_customer.items(), key=lambda kv: (-kv[1], kv[0].lower())
        )
    ]

    return CourierEarningsResponse(
        month=month_label,
        charge_cents=rate.charge_cents,
        platform_cents=rate.platform_cents,
        courier_cents=rate.courier_cents,
        total_boxes=total_boxes,
        total_charge=round(total_boxes * rate.charge_cents / 100, 2),
        total_platform=round(total_boxes * rate.platform_cents / 100, 2),
        total_courier=round(total_boxes * rate.courier_cents / 100, 2),
        customers=customers,
    )
