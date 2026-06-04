"""Tests for admin courier billing: rate-card CRUD and settlements."""

import datetime

from app.models import Booking, CourierRateCard, Customer, Order, OrderLine, SKU
from tests.conftest import auth_header


def _card_body(**over):
    body = {
        "charge_cents": 50, "platform_cents": 17, "courier_cents": 33,
        "effective_from": "2026-01-01",
    }
    body.update(over)
    return body


def _seed_bookings(db, courier_id, *, customer_name, sku_code, count, when,
                   status="closed", category="wine"):
    sku = SKU(sku_code=sku_code, name=f"P {sku_code}", category=category)
    db.add(sku)
    db.flush()
    customer = Customer(name=customer_name)
    db.add(customer)
    db.flush()
    order = Order(reference=f"ORD-{sku_code}", status=status, delivery_week="2026-W21")
    db.add(order)
    db.flush()
    line = OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                     klant=customer.name, quantity=count, booked_count=count)
    db.add(line)
    db.flush()
    for _ in range(count):
        db.add(Booking(order_id=order.id, order_line_id=line.id, sku_id=sku.id,
                       scanned_by=courier_id, created_at=when))
    db.commit()
    return customer


# --- Rate cards ---
def test_create_rate_card(client, admin_token):
    resp = client.post("/api/courier/rate-cards", json=_card_body(),
                       headers=auth_header(admin_token))
    assert resp.status_code == 201
    body = resp.json()
    assert body["charge_cents"] == 50
    assert body["id"] > 0
    assert body["unit_type"] == "box"


def test_create_rate_card_bad_split_rejected(client, admin_token):
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(charge_cents=50, platform_cents=20, courier_cents=20),
                       headers=auth_header(admin_token))
    assert resp.status_code == 422


def test_create_rate_card_invalid_service_rejected(client, admin_token):
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(service_type="boek"),
                       headers=auth_header(admin_token))
    assert resp.status_code == 422


def test_rate_card_overlap_rejected(client, admin_token):
    client.post("/api/courier/rate-cards",
                json=_card_body(effective_from="2026-01-01", effective_until="2026-06-01"),
                headers=auth_header(admin_token))
    # Same (global) scope, overlapping range.
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(effective_from="2026-05-01"),
                       headers=auth_header(admin_token))
    assert resp.status_code == 409


def test_rate_card_non_overlapping_allowed(client, admin_token):
    client.post("/api/courier/rate-cards",
                json=_card_body(effective_from="2026-01-01", effective_until="2026-06-01"),
                headers=auth_header(admin_token))
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(effective_from="2026-06-01"),
                       headers=auth_header(admin_token))
    assert resp.status_code == 201


def test_different_scope_same_dates_allowed(client, admin_token, courier_user):
    client.post("/api/courier/rate-cards", json=_card_body(),
                headers=auth_header(admin_token))
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(courier_id=courier_user.id),
                       headers=auth_header(admin_token))
    assert resp.status_code == 201


def test_rate_card_requires_admin(client, courier_token):
    resp = client.post("/api/courier/rate-cards", json=_card_body(),
                       headers=auth_header(courier_token))
    assert resp.status_code == 403


def test_rate_card_rejects_non_courier(client, admin_token, owner_user):
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(courier_id=owner_user.id),
                       headers=auth_header(admin_token))
    assert resp.status_code == 422


def test_rate_card_rejects_customer_org_mismatch(client, db, admin_token, sample_org):
    cust = Customer(name="X", organization_id=sample_org.id)
    db.add(cust)
    db.commit()
    resp = client.post("/api/courier/rate-cards",
                       json=_card_body(customer_id=cust.id,
                                       organization_id=sample_org.id + 999),
                       headers=auth_header(admin_token))
    assert resp.status_code == 422


def test_update_and_delete_rate_card(client, admin_token):
    cid = client.post("/api/courier/rate-cards", json=_card_body(),
                      headers=auth_header(admin_token)).json()["id"]
    upd = client.patch(f"/api/courier/rate-cards/{cid}",
                       json=_card_body(charge_cents=60, platform_cents=20, courier_cents=40),
                       headers=auth_header(admin_token))
    assert upd.status_code == 200
    assert upd.json()["charge_cents"] == 60
    dele = client.delete(f"/api/courier/rate-cards/{cid}", headers=auth_header(admin_token))
    assert dele.status_code == 204


# --- Settlements ---
def test_create_settlement_snapshots_lines(client, db, admin_token, courier_user):
    db.add(CourierRateCard(unit_type="box", charge_cents=50, platform_cents=17,
                           courier_cents=33, effective_from=datetime.date(2000, 1, 1)))
    db.commit()
    _seed_bookings(db, courier_user.id, customer_name="Cafe A", sku_code="A1",
                   count=10, when=datetime.datetime(2026, 5, 12, 9, 0))

    resp = client.post("/api/courier/settlements",
                       json={"courier_id": courier_user.id, "month": "2026-05"},
                       headers=auth_header(admin_token))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["total_units"] == 10
    assert body["total_charge_cents"] == 500
    assert body["total_platform_cents"] == 170
    assert body["total_courier_cents"] == 330
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["units"] == 10
    assert line["scope"] == "global_default"
    assert line["line_courier_cents"] == 330


def test_settlement_blocks_unrated_units(client, db, admin_token, courier_user):
    # No rate card at all → units have no tariff.
    _seed_bookings(db, courier_user.id, customer_name="A", sku_code="A1", count=3,
                   when=datetime.datetime(2026, 5, 12, 9, 0))
    resp = client.post("/api/courier/settlements",
                       json={"courier_id": courier_user.id, "month": "2026-05"},
                       headers=auth_header(admin_token))
    assert resp.status_code == 422


def test_settlement_allow_unrated_override(client, db, admin_token, courier_user):
    _seed_bookings(db, courier_user.id, customer_name="A", sku_code="A1", count=3,
                   when=datetime.datetime(2026, 5, 12, 9, 0))
    resp = client.post(
        "/api/courier/settlements",
        json={"courier_id": courier_user.id, "month": "2026-05", "allow_unrated": True},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_units"] == 3
    assert body["total_charge_cents"] == 0
    assert body["lines"][0]["scope"] == "unrated"


def test_settlement_duplicate_rejected(client, db, admin_token, courier_user):
    db.add(CourierRateCard(unit_type="box", charge_cents=50, platform_cents=17,
                           courier_cents=33, effective_from=datetime.date(2000, 1, 1)))
    db.commit()
    _seed_bookings(db, courier_user.id, customer_name="A", sku_code="A1", count=2,
                   when=datetime.datetime(2026, 5, 12, 9, 0))
    payload = {"courier_id": courier_user.id, "month": "2026-05"}
    assert client.post("/api/courier/settlements", json=payload,
                       headers=auth_header(admin_token)).status_code == 201
    assert client.post("/api/courier/settlements", json=payload,
                       headers=auth_header(admin_token)).status_code == 409


def test_settlement_snapshot_survives_rate_card_change(client, db, admin_token, courier_user):
    card = CourierRateCard(unit_type="box", charge_cents=50, platform_cents=17,
                           courier_cents=33, effective_from=datetime.date(2000, 1, 1))
    db.add(card)
    db.commit()
    _seed_bookings(db, courier_user.id, customer_name="A", sku_code="A1", count=4,
                   when=datetime.datetime(2026, 5, 12, 9, 0))
    sid = client.post("/api/courier/settlements",
                      json={"courier_id": courier_user.id, "month": "2026-05"},
                      headers=auth_header(admin_token)).json()["id"]

    # Delete the rate card after settling.
    client.delete(f"/api/courier/rate-cards/{card.id}", headers=auth_header(admin_token))

    body = client.get(f"/api/courier/settlements/{sid}",
                      headers=auth_header(admin_token)).json()
    # The snapshot is the point: amounts survive the card being deleted.
    # (In Postgres the rate_card_id FK is additionally SET NULL on delete.)
    assert body["total_charge_cents"] == 200  # unchanged
    assert body["lines"][0]["charge_cents"] == 50
    assert body["lines"][0]["units"] == 4


def test_settlement_approve_pay_flow(client, db, admin_token, courier_user):
    db.add(CourierRateCard(unit_type="box", charge_cents=50, platform_cents=17,
                           courier_cents=33, effective_from=datetime.date(2000, 1, 1)))
    db.commit()
    _seed_bookings(db, courier_user.id, customer_name="A", sku_code="A1", count=1,
                   when=datetime.datetime(2026, 5, 12, 9, 0))
    sid = client.post("/api/courier/settlements",
                      json={"courier_id": courier_user.id, "month": "2026-05"},
                      headers=auth_header(admin_token)).json()["id"]

    # Cannot pay before approve.
    assert client.post(f"/api/courier/settlements/{sid}/pay",
                       headers=auth_header(admin_token)).status_code == 400
    appr = client.post(f"/api/courier/settlements/{sid}/approve",
                       headers=auth_header(admin_token))
    assert appr.status_code == 200 and appr.json()["status"] == "approved"
    paid = client.post(f"/api/courier/settlements/{sid}/pay",
                       headers=auth_header(admin_token))
    assert paid.status_code == 200 and paid.json()["status"] == "paid"


def test_settlement_requires_admin(client, courier_token, courier_user):
    resp = client.post("/api/courier/settlements",
                       json={"courier_id": courier_user.id, "month": "2026-05"},
                       headers=auth_header(courier_token))
    assert resp.status_code == 403
