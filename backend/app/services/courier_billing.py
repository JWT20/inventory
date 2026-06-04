"""Shared courier billing aggregation.

Resolves a tariff per booking and groups the results, so the (live) earnings
view and the (frozen) settlement run compute identical numbers.
"""

import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import (
    SKU,
    Booking,
    CourierRateCard,
    Order,
    OrderLine,
)
from app.services.rate_resolver import resolve_rate, service_for_category

# Only finalised orders are invoiceable: billed once fully picked (completed)
# or deliberately wrapped up (closed). Open/cancelled orders are excluded.
BILLABLE_ORDER_STATUSES = ("completed", "closed")


@dataclass
class BillingGroup:
    customer_id: int | None
    organization_id: int | None
    customer_name: str
    service_type: str
    unit_type: str
    units: int
    charge_cents: int  # per unit
    platform_cents: int  # per unit
    courier_cents: int  # per unit
    rate_card_id: int | None
    scope: str


def compute_courier_groups(
    db: Session,
    courier_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[BillingGroup]:
    """Aggregate a courier's billable bookings in [start, end) into priced
    groups keyed by customer identity + service type + applied rate card."""
    rows = (
        db.query(
            OrderLine.customer_id,
            OrderLine.klant,
            Order.organization_id,
            SKU.category,
            Booking.created_at,
        )
        .join(OrderLine, Booking.order_line_id == OrderLine.id)
        .join(Order, Booking.order_id == Order.id)
        .join(SKU, Booking.sku_id == SKU.id)
        .filter(
            Booking.scanned_by == courier_id,
            Booking.created_at >= start,
            Booking.created_at < end,
            Order.status.in_(BILLABLE_ORDER_STATUSES),
        )
        .all()
    )

    cards = db.query(CourierRateCard).all()

    groups: dict[tuple, BillingGroup] = {}
    for customer_id, klant, organization_id, category, created_at in rows:
        service_type, _unit = service_for_category(category)
        on_date = (created_at or datetime.datetime.min).date()
        resolved = resolve_rate(
            cards,
            courier_id=courier_id,
            customer_id=customer_id,
            organization_id=organization_id,
            service_type=service_type,
            on_date=on_date,
        )
        if resolved is None:
            # No tariff configured — surface the units at zero so the gap is
            # visible rather than silently dropped.
            charge = platform = courier = 0
            rate_card_id = None
            scope = "unrated"
            unit_type = _unit
        else:
            charge = resolved.charge_cents
            platform = resolved.platform_cents
            courier = resolved.courier_cents
            rate_card_id = resolved.rate_card_id
            scope = resolved.scope
            unit_type = resolved.unit_type

        if customer_id is not None:
            ident = ("c", customer_id)
            name = klant or "Onbekend"
        else:
            name = klant or "Onbekend"
            ident = ("k", organization_id, name)
        # Split by rate card so a mid-month tariff change shows as two groups.
        key = (ident, service_type, rate_card_id)

        group = groups.get(key)
        if group is None:
            group = groups[key] = BillingGroup(
                customer_id=customer_id,
                organization_id=organization_id,
                customer_name=name,
                service_type=service_type,
                unit_type=unit_type,
                units=0,
                charge_cents=charge,
                platform_cents=platform,
                courier_cents=courier,
                rate_card_id=rate_card_id,
                scope=scope,
            )
        group.units += 1

    return sorted(
        groups.values(),
        key=lambda g: (-g.units, g.customer_name.lower(), g.service_type),
    )
