"""Tests for the courier per-month box invoicing endpoint."""

import datetime

from app.models import Booking, CourierBillingRate, Customer, Order, OrderLine, SKU
from tests.conftest import auth_header


def _seed_bookings(db, courier_id, *, customer_name, sku_code, count, when):
    sku = SKU(sku_code=sku_code, name=f"Wine {sku_code}")
    db.add(sku)
    db.flush()
    customer = Customer(name=customer_name)
    db.add(customer)
    db.flush()
    order = Order(reference=f"ORD-{sku_code}", status="closed", delivery_week="2026-W21")
    db.add(order)
    db.flush()
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=count,
        booked_count=count,
    )
    db.add(line)
    db.flush()
    for _ in range(count):
        db.add(
            Booking(
                order_id=order.id,
                order_line_id=line.id,
                sku_id=sku.id,
                scanned_by=courier_id,
                created_at=when,
            )
        )
    db.commit()


def test_earnings_counts_boxes_and_splits_amounts(client, db, courier_user, courier_token):
    _seed_bookings(
        db, courier_user.id, customer_name="Cafe A", sku_code="A1", count=10,
        when=datetime.datetime(2026, 5, 12, 9, 0),
    )

    resp = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-05"
    assert body["total_boxes"] == 10
    # Default rate 50/17/33 cents per box.
    assert body["total_charge"] == 5.0
    assert body["total_platform"] == 1.7
    assert body["total_courier"] == 3.3
    assert body["customers"] == [
        {"customer_name": "Cafe A", "boxes": 10, "charge_amount": 5.0}
    ]


def test_earnings_filters_by_month(client, db, courier_user, courier_token):
    _seed_bookings(
        db, courier_user.id, customer_name="Cafe A", sku_code="A1", count=4,
        when=datetime.datetime(2026, 5, 31, 23, 0),
    )
    _seed_bookings(
        db, courier_user.id, customer_name="Cafe B", sku_code="B1", count=7,
        when=datetime.datetime(2026, 6, 1, 1, 0),
    )

    may = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()
    june = client.get(
        "/api/courier/earnings?month=2026-06", headers=auth_header(courier_token)
    ).json()

    assert may["total_boxes"] == 4
    assert june["total_boxes"] == 7


def test_earnings_only_counts_own_scans(client, db, courier_user, courier_token, owner_user):
    _seed_bookings(
        db, owner_user.id, customer_name="Other", sku_code="X1", count=5,
        when=datetime.datetime(2026, 5, 10, 9, 0),
    )

    body = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()

    assert body["total_boxes"] == 0
    assert body["customers"] == []


def test_earnings_uses_configured_rate(client, db, courier_user, courier_token):
    db.add(CourierBillingRate(id=1, charge_cents=60, platform_cents=20, courier_cents=40))
    _seed_bookings(
        db, courier_user.id, customer_name="Cafe A", sku_code="A1", count=3,
        when=datetime.datetime(2026, 5, 12, 9, 0),
    )

    body = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()

    assert body["charge_cents"] == 60
    assert body["total_charge"] == 1.8
    assert body["total_platform"] == 0.6
    assert body["total_courier"] == 1.2


def test_earnings_rejects_non_warehouse(client, db, owner_token):
    resp = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(owner_token)
    )
    assert resp.status_code == 403


def test_earnings_invalid_month(client, courier_token):
    resp = client.get(
        "/api/courier/earnings?month=not-a-month", headers=auth_header(courier_token)
    )
    assert resp.status_code == 422
