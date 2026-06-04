"""Tests for the courier per-month invoicing endpoint (rate-card driven)."""

import datetime

from app.models import Booking, CourierRateCard, Customer, Order, OrderLine, SKU
from tests.conftest import auth_header


def _global_card(db, charge=50, platform=17, courier=33):
    """The catch-all rate card the migration normally seeds."""
    card = CourierRateCard(
        unit_type="box", charge_cents=charge, platform_cents=platform,
        courier_cents=courier, effective_from=datetime.date(2000, 1, 1),
    )
    db.add(card)
    db.commit()
    return card


def _seed_bookings(
    db, courier_id, *, customer_name, sku_code, count, when, status="closed",
    organization_id=None, category="wine",
):
    sku = SKU(sku_code=sku_code, name=f"Wine {sku_code}", category=category)
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
        order_id=order.id, sku_id=sku.id, customer_id=customer.id,
        klant=customer.name, quantity=count, booked_count=count,
    )
    db.add(line)
    db.flush()
    for _ in range(count):
        db.add(Booking(
            order_id=order.id, order_line_id=line.id, sku_id=sku.id,
            scanned_by=courier_id, created_at=when,
        ))
    db.commit()
    return customer


def test_earnings_counts_units_and_charge(client, db, courier_user, courier_token):
    _global_card(db)
    _seed_bookings(db, courier_user.id, customer_name="Cafe A", sku_code="A1",
                   count=10, when=datetime.datetime(2026, 5, 12, 9, 0))

    resp = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-05"
    assert body["total_units"] == 10
    assert body["total_charge"] == 5.0
    # No platform/courier split leaks to the courier.
    assert "total_platform" not in body
    assert "total_courier" not in body
    assert len(body["groups"]) == 1
    g = body["groups"][0]
    assert g["customer_name"] == "Cafe A"
    assert g["units"] == 10
    assert g["service_type"] == "wine_pick"
    assert g["charge_amount"] == 5.0
    assert g["customer_id"] is not None


def test_earnings_filters_by_month(client, db, courier_user, courier_token):
    _global_card(db)
    _seed_bookings(db, courier_user.id, customer_name="Cafe A", sku_code="A1",
                   count=4, when=datetime.datetime(2026, 5, 31, 23, 0))
    _seed_bookings(db, courier_user.id, customer_name="Cafe B", sku_code="B1",
                   count=7, when=datetime.datetime(2026, 6, 1, 1, 0))

    may = client.get("/api/courier/earnings?month=2026-05",
                     headers=auth_header(courier_token)).json()
    june = client.get("/api/courier/earnings?month=2026-06",
                      headers=auth_header(courier_token)).json()
    assert may["total_units"] == 4
    assert june["total_units"] == 7


def test_earnings_excludes_unfinished_and_cancelled(client, db, courier_user, courier_token):
    _global_card(db)
    for status in ("active", "pending_images", "draft", "cancelled"):
        _seed_bookings(db, courier_user.id, customer_name=f"C {status}",
                       sku_code=f"S-{status}", count=3,
                       when=datetime.datetime(2026, 5, 12, 9, 0), status=status)

    body = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token)).json()
    assert body["total_units"] == 0
    assert body["groups"] == []


def test_earnings_only_counts_own_scans(client, db, courier_user, courier_token, owner_user):
    _global_card(db)
    _seed_bookings(db, owner_user.id, customer_name="Other", sku_code="X1",
                   count=5, when=datetime.datetime(2026, 5, 10, 9, 0))
    body = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token)).json()
    assert body["total_units"] == 0


def test_earnings_per_customer_rate_card(client, db, courier_user, courier_token):
    """Courier A: customer X €0.50, customer Y €0.70."""
    _global_card(db)  # default 0.50
    cust_y = _seed_bookings(db, courier_user.id, customer_name="Cafe Y",
                            sku_code="Y1", count=2,
                            when=datetime.datetime(2026, 5, 12, 9, 0))
    _seed_bookings(db, courier_user.id, customer_name="Cafe X", sku_code="X1",
                   count=3, when=datetime.datetime(2026, 5, 12, 9, 0))
    # Special €0.70 card for customer Y.
    db.add(CourierRateCard(
        courier_id=courier_user.id, customer_id=cust_y.id, unit_type="box",
        charge_cents=70, platform_cents=20, courier_cents=50,
        effective_from=datetime.date(2000, 1, 1),
    ))
    db.commit()

    body = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token)).json()
    by_name = {g["customer_name"]: g for g in body["groups"]}
    assert by_name["Cafe X"]["charge_amount"] == 1.5   # 3 * 0.50
    assert by_name["Cafe Y"]["charge_amount"] == 1.4   # 2 * 0.70
    assert body["total_charge"] == 2.9


def test_earnings_book_vs_wine_service(client, db, courier_user, courier_token):
    _global_card(db)  # wine default 0.50
    _seed_bookings(db, courier_user.id, customer_name="Shop", sku_code="W1",
                   count=2, when=datetime.datetime(2026, 5, 12, 9, 0),
                   category="wine")
    _seed_bookings(db, courier_user.id, customer_name="Shop2", sku_code="B1",
                   count=4, when=datetime.datetime(2026, 5, 12, 9, 0),
                   category="book")
    # Book pick rate 0.20.
    db.add(CourierRateCard(
        service_type="book_pick", unit_type="item", charge_cents=20,
        platform_cents=5, courier_cents=15, effective_from=datetime.date(2000, 1, 1),
    ))
    db.commit()

    body = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token)).json()
    by_service = {g["service_type"]: g for g in body["groups"]}
    assert by_service["wine_pick"]["charge_amount"] == 1.0   # 2 * 0.50
    assert by_service["book_pick"]["charge_amount"] == 0.8   # 4 * 0.20
    assert by_service["book_pick"]["unit_type"] == "item"


def test_earnings_separates_same_name_across_orgs(client, db, courier_user, courier_token):
    _global_card(db)
    _seed_bookings(db, courier_user.id, customer_name="Vino", sku_code="O1",
                   count=4, when=datetime.datetime(2026, 5, 12, 9, 0),
                   organization_id=1)
    _seed_bookings(db, courier_user.id, customer_name="Vino", sku_code="O2",
                   count=6, when=datetime.datetime(2026, 5, 12, 9, 0),
                   organization_id=2)
    body = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(courier_token)).json()
    assert body["total_units"] == 10
    assert len(body["groups"]) == 2


def test_earnings_rejects_non_warehouse(client, db, owner_token):
    resp = client.get("/api/courier/earnings?month=2026-05",
                      headers=auth_header(owner_token))
    assert resp.status_code == 403


def test_earnings_invalid_month(client, courier_token):
    resp = client.get("/api/courier/earnings?month=not-a-month",
                      headers=auth_header(courier_token))
    assert resp.status_code == 422
