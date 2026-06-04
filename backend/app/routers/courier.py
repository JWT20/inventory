"""Courier-facing endpoint: per-month box scanning earnings / invoicing.

A courier invoices the customer per scanned box/item (1 Booking = 1 unit). The
tariff is resolved per booking from the courier rate cards. This screen stays
free of the platform/courier split — that is admin-facing (see courier_billing
router).
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_warehouse
from app.database import get_db
from app.models import User
from app.schemas import CourierEarningsGroup, CourierEarningsResponse
from app.services.courier_billing import compute_courier_groups

router = APIRouter(prefix="/courier", tags=["courier"])


def month_bounds(month: str | None) -> tuple[datetime.datetime, datetime.datetime, str]:
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


@router.get("/earnings", response_model=CourierEarningsResponse)
def courier_earnings(
    month: str | None = Query(None, description="Maand als YYYY-MM, standaard huidige maand"),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Units the current courier scanned in a month, grouped per customer/service."""
    start, end, month_label = month_bounds(month)
    groups = compute_courier_groups(db, user.id, start, end)

    out = [
        CourierEarningsGroup(
            customer_id=g.customer_id,
            organization_id=g.organization_id,
            customer_name=g.customer_name,
            service_type=g.service_type,
            unit_type=g.unit_type,
            units=g.units,
            charge_amount=round(g.units * g.charge_cents / 100, 2),
        )
        for g in groups
    ]
    total_units = sum(g.units for g in groups)
    total_charge = round(sum(g.units * g.charge_cents for g in groups) / 100, 2)

    return CourierEarningsResponse(
        month=month_label,
        total_units=total_units,
        total_charge=total_charge,
        groups=out,
    )
