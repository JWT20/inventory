"""Tests for box/bottle splits in the weekly order summary."""

from app.models import Customer, Order, OrderLine, SKU
from tests.conftest import auth_header

WEEK = "2026-W30"


def _seed_mixed_order(db, org):
    box_sku = SKU(sku_code="WS-BOX", name="Doos Wijn")
    bottle_sku = SKU(sku_code="WS-FLES", name="Cava 0,0", is_bottle=True)
    customer = Customer(name="Week Klant", organization_id=org.id)
    db.add_all([box_sku, bottle_sku, customer])
    db.flush()
    order = Order(
        organization_id=org.id,
        reference="WS-1",
        status="active",
        delivery_week=WEEK,
    )
    db.add(order)
    db.flush()
    db.add_all([
        OrderLine(
            order_id=order.id, sku_id=box_sku.id, customer_id=customer.id,
            klant=customer.name, quantity=6, booked_count=0,
        ),
        OrderLine(
            order_id=order.id, sku_id=bottle_sku.id, customer_id=customer.id,
            klant=customer.name, quantity=2, booked_count=0,
        ),
    ])
    db.commit()


def test_supplier_grouping_splits_boxes_and_bottles(
    client, db, owner_token, sample_org
):
    _seed_mixed_order(db, sample_org)

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "supplier"},
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["grand_total_quantity"] == 8
    assert data["grand_total_boxes"] == 6
    assert data["grand_total_bottles"] == 2

    supplier = data["suppliers"][0]
    assert supplier["supplier_total_boxes"] == 6
    assert supplier["supplier_total_bottles"] == 2
    wines = {w["sku_code"]: w for w in supplier["wines"]}
    assert wines["WS-BOX"]["is_bottle"] is False
    assert wines["WS-FLES"]["is_bottle"] is True


def test_customer_grouping_splits_boxes_and_bottles(
    client, db, owner_token, sample_org
):
    _seed_mixed_order(db, sample_org)

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "customer"},
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["grand_total_boxes"] == 6
    assert data["grand_total_bottles"] == 2

    cust = data["customers"][0]
    assert cust["customer_total_quantity"] == 8
    assert cust["customer_total_boxes"] == 6
    assert cust["customer_total_bottles"] == 2
    lines = {l["sku_code"]: l for l in cust["lines"]}
    assert lines["WS-BOX"]["is_bottle"] is False
    assert lines["WS-FLES"]["is_bottle"] is True


def test_weekly_summary_denies_customer(
    client, db, customer_user, customer_token, sample_org
):
    """The weekly summary exposes pricing across all customers in the org, so
    customer-role users must not reach it (even though the UI hides the tab)."""
    _seed_mixed_order(db, sample_org)
    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "customer"},
        headers=auth_header(customer_token),
    )
    assert resp.status_code == 403


def test_weekly_summary_denies_courier(
    client, db, courier_user, courier_token, sample_org
):
    _seed_mixed_order(db, sample_org)
    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "supplier"},
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403
