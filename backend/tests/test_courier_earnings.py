"""Tests for the courier per-month box invoicing endpoint."""

import datetime

from app.models import Booking, CourierBillingRate, Customer, Order, OrderLine, SKU
from tests.conftest import auth_header


def _seed_bookings(
    db, courier_id, *, customer_name, sku_code, count, when, status="closed",
    organization_id=None,
):
    sku = SKU(sku_code=sku_code, name=f"Wine {sku_code}")
    db.add(sku)
    db.flush()
    customer = Customer(name=customer_name, organization_id=organization_id)
    db.add(customer)
    db.flush()
    order = Order(
        reference=f"ORD-{sku_code}", status=status, delivery_week="2026-W21",
        organization_id=organization_id,
    )
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


def test_earnings_counts_boxes_and_charge(client, db, courier_user, courier_token):
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
    # Default rate: 50 cents per box, charged to the customer.
    assert body["total_charge"] == 5.0
    # The platform/courier split is admin-facing and must NOT leak here.
    assert "total_platform" not in body
    assert "total_courier" not in body
    assert "platform_cents" not in body
    assert "courier_cents" not in body
    assert len(body["customers"]) == 1
    row = body["customers"][0]
    assert row["customer_name"] == "Cafe A"
    assert row["boxes"] == 10
    assert row["charge_amount"] == 5.0
    assert row["customer_id"] is not None


def test_earnings_separates_same_name_customers_across_orgs(
    client, db, courier_user, courier_token
):
    _seed_bookings(
        db, courier_user.id, customer_name="Vino", sku_code="O1", count=4,
        when=datetime.datetime(2026, 5, 12, 9, 0), organization_id=1,
    )
    _seed_bookings(
        db, courier_user.id, customer_name="Vino", sku_code="O2", count=6,
        when=datetime.datetime(2026, 5, 12, 9, 0), organization_id=2,
    )

    body = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()

    assert body["total_boxes"] == 10
    # Same name, different orgs → two distinct invoice rows, not merged.
    assert len(body["customers"]) == 2
    by_boxes = sorted(body["customers"], key=lambda c: c["boxes"])
    assert by_boxes[0]["boxes"] == 4 and by_boxes[1]["boxes"] == 6
    assert by_boxes[0]["customer_id"] != by_boxes[1]["customer_id"]


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


def test_earnings_counts_completed_orders(client, db, courier_user, courier_token):
    _seed_bookings(
        db, courier_user.id, customer_name="Cafe A", sku_code="A1", count=6,
        when=datetime.datetime(2026, 5, 12, 9, 0), status="completed",
    )

    body = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()

    assert body["total_boxes"] == 6


def test_earnings_excludes_unfinished_and_cancelled_orders(client, db, courier_user, courier_token):
    for status in ("active", "pending_images", "draft", "cancelled"):
        _seed_bookings(
            db, courier_user.id, customer_name=f"Cafe {status}", sku_code=f"S-{status}",
            count=3, when=datetime.datetime(2026, 5, 12, 9, 0), status=status,
        )

    body = client.get(
        "/api/courier/earnings?month=2026-05", headers=auth_header(courier_token)
    ).json()

    assert body["total_boxes"] == 0
    assert body["customers"] == []


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
