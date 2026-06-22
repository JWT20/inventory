"""Tests for order provenance + born-active semantics (Fase 1, PR-B).

- Order.channel defaults to "manual"; (org, channel, external_id) is unique so
  the same channel order cannot be imported twice.
- An order awaits approval only when its organization runs the weekly approval
  flow (week_overview). Merchants without it get born-active orders (no week).
- Born-active weekless orders must not leak into the wine week views.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.auth import create_token, hash_password
from app.models import Customer, Order, OrderLine, Organization, SKU, User
from tests.conftest import auth_header


# --- helpers ---------------------------------------------------------------

def _make_org(db, slug, modules):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_owner(db, org, username):
    user = User(
        username=username,
        email=f"{username}@local",
        hashed_password=hash_password("OwnerPass1!"),
        role="owner",
        organization_id=org.id,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_token(user.id)


def _make_customer_and_sku(db, org, code):
    customer = Customer(name=f"Klant {code}", organization_id=org.id)
    sku = SKU(sku_code=code, name=f"Product {code}", organization_id=org.id)
    db.add_all([customer, sku])
    db.commit()
    db.refresh(customer)
    db.refresh(sku)
    return customer, sku


def _create_order(client, token, org, customer, sku):
    return client.post(
        "/api/orders",
        json={
            "organization_id": org.id,
            "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 3}],
        },
        headers=auth_header(token),
    )


# --- born-active vs approval semantics ------------------------------------

def test_org_without_week_overview_gets_born_active_order(client, db):
    org = _make_org(db, "socks", ["inventory", "orders", "barcode_picking"])
    token = _make_owner(db, org, "socks-owner")
    customer, sku = _make_customer_and_sku(db, org, "SOCK-1")

    resp = _create_order(client, token, org, customer, sku)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"  # no approval step → ready to pick
    assert data["delivery_week"] is None
    assert data["channel"] == "manual"


def test_org_with_week_overview_still_awaits_approval(client, db):
    org = _make_org(db, "wine", ["inventory", "orders", "vision_picking", "week_overview"])
    token = _make_owner(db, org, "wine-owner")
    customer, sku = _make_customer_and_sku(db, org, "WINE-1")

    resp = _create_order(client, token, org, customer, sku)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending_approval"
    assert data["channel"] == "manual"


# --- channel + dedup -------------------------------------------------------

def test_channel_defaults_to_manual(db):
    org = _make_org(db, "any", ["inventory", "orders", "barcode_picking"])
    order = Order(organization_id=org.id, reference="ORD-X", status="active")
    db.add(order)
    db.commit()
    db.refresh(order)
    assert order.channel == "manual"
    assert order.external_id is None


def test_duplicate_channel_order_is_rejected(db):
    org = _make_org(db, "shop", ["inventory", "orders", "barcode_picking", "channel_orders"])
    db.add(Order(organization_id=org.id, reference="ORD-A", status="active",
                 channel="shopify", external_id="SHOP-1001"))
    db.commit()

    # Same (org, channel, external_id) — the partial unique index must reject it.
    db.add(Order(organization_id=org.id, reference="ORD-B", status="active",
                 channel="shopify", external_id="SHOP-1001"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_manual_orders_never_collide_on_null_external_id(db):
    org = _make_org(db, "manual-org", ["inventory", "orders", "barcode_picking"])
    db.add(Order(organization_id=org.id, reference="ORD-M1", status="active"))
    db.add(Order(organization_id=org.id, reference="ORD-M2", status="active"))
    # Both have channel="manual", external_id=NULL → excluded from the index.
    db.commit()  # does not raise


# --- born-active orders stay out of the wine week views -------------------

def _seed_active_line(db, org, code, week=None):
    customer, sku = _make_customer_and_sku(db, org, code)
    order = Order(
        organization_id=org.id, reference=f"WK-{code}", status="active",
        delivery_week=week,
    )
    db.add(order)
    db.flush()
    db.add(OrderLine(
        order_id=order.id, sku_id=sku.id, customer_id=customer.id,
        klant=customer.name, quantity=4, booked_count=0, delivery_day="wednesday",
    ))
    db.commit()
    return sku


def test_weekly_pick_photos_excludes_born_active_channel_order(client, db, courier_token):
    # Wine org (week-planning): a weekless active order created this week still
    # shows via the fallback. Socks org (no week_overview): must NOT show.
    wine = _make_org(db, "wine-wk", ["inventory", "orders", "vision_picking", "week_overview"])
    socks = _make_org(db, "socks-wk", ["inventory", "orders", "barcode_picking"])
    wine_sku = _seed_active_line(db, wine, "WINEWK")
    socks_sku = _seed_active_line(db, socks, "SOCKWK")

    resp = client.get("/api/orders/weekly-pick-photos", headers=auth_header(courier_token))
    assert resp.status_code == 200
    sku_ids = {item["sku_id"] for item in resp.json()}
    assert wine_sku.id in sku_ids
    assert socks_sku.id not in sku_ids
