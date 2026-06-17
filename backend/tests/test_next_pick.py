"""Tests for the next-pick suggestion endpoint used by Scan & Boek."""

from app.models import Customer, Order, OrderLine, Organization, SKU
from tests.conftest import auth_header


def _make_sku(db, code="SKU-1"):
    sku = SKU(sku_code=code, name=f"Wine {code}")
    db.add(sku)
    db.flush()
    return sku


def _make_customer(db, org, name):
    customer = Customer(name=name, organization_id=org.id)
    db.add(customer)
    db.flush()
    return customer


def _make_order(db, org, ref, week="2026-W21", status="active"):
    order = Order(
        organization_id=org.id,
        reference=ref,
        status=status,
        delivery_week=week,
    )
    db.add(order)
    db.flush()
    return order


def _make_line(db, order, sku, customer, quantity, booked_count=0):
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=quantity,
        booked_count=booked_count,
        delivery_day="wednesday",
    )
    db.add(line)
    db.flush()
    return line


def _get(client, token, order_id):
    return client.get(
        f"/api/orders/{order_id}/next-pick",
        headers=auth_header(token),
    )


def test_next_pick_returns_first_open_line_in_this_order(
    client, db, courier_token, sample_org
):
    sku_a = _make_sku(db, "A")
    sku_b = _make_sku(db, "B")
    cust = _make_customer(db, sample_org, "Alpha")
    order = _make_order(db, sample_org, "CTX")
    # First line fully booked, second still open — expect the open one.
    _make_line(db, order, sku_a, cust, quantity=2, booked_count=2)
    open_line = _make_line(db, order, sku_b, cust, quantity=3, booked_count=1)
    db.commit()

    resp = _get(client, courier_token, order.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "this_order"
    assert body["sku_id"] == sku_b.id
    assert body["order_line_id"] == open_line.id
    assert body["order_id"] == order.id
    assert body["remaining_quantity"] == 2
    assert body["customer_name"] == "Alpha"


def test_next_pick_falls_back_to_other_order_when_full(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust_a = _make_customer(db, sample_org, "Alpha")
    cust_b = _make_customer(db, sample_org, "Bravo")
    full_order = _make_order(db, sample_org, "FULL")
    other_order = _make_order(db, sample_org, "OTHER")
    _make_line(db, full_order, sku, cust_a, quantity=2, booked_count=2)
    other_line = _make_line(db, other_order, sku, cust_b, quantity=4, booked_count=1)
    db.commit()

    resp = _get(client, courier_token, full_order.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "other_order"
    assert body["order_id"] == other_order.id
    assert body["order_line_id"] == other_line.id
    assert body["customer_name"] == "Bravo"
    assert body["remaining_quantity"] == 3


def test_next_pick_other_order_scoped_to_same_week_and_org(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    other_org = Organization(name="Andere Wijnhandel", slug="andere-np")
    db.add(other_org)
    db.flush()
    foreign_cust = _make_customer(db, other_org, "Foreign")

    full_order = _make_order(db, sample_org, "FULL", week="2026-W21")
    _make_line(db, full_order, sku, cust, quantity=1, booked_count=1)
    # Open lines exist, but in another week and another org — both excluded.
    next_week = _make_order(db, sample_org, "NEXT", week="2026-W22")
    _make_line(db, next_week, sku, cust, quantity=2)
    foreign = _make_order(db, other_org, "FOREIGN", week="2026-W21")
    _make_line(db, foreign, sku, foreign_cust, quantity=2)
    db.commit()

    resp = _get(client, courier_token, full_order.id)
    assert resp.status_code == 200
    assert resp.json() is None


def test_next_pick_skips_inactive_other_orders(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    full_order = _make_order(db, sample_org, "FULL")
    completed = _make_order(db, sample_org, "DONE", status="completed")
    active_open = _make_order(db, sample_org, "OPEN")
    _make_line(db, full_order, sku, cust, quantity=1, booked_count=1)
    _make_line(db, completed, sku, cust, quantity=3)  # open but not active
    active_line = _make_line(db, active_open, sku, cust, quantity=3)
    db.commit()

    resp = _get(client, courier_token, full_order.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "other_order"
    assert body["order_line_id"] == active_line.id


def test_next_pick_rejects_other_org_for_owner(client, db, owner_token, sample_org):
    other_org = Organization(name="Andere Wijnhandel", slug="andere-np2")
    db.add(other_org)
    db.flush()
    sku = _make_sku(db)
    cust = _make_customer(db, other_org, "Foreign")
    order = _make_order(db, other_org, "FOREIGN")
    _make_line(db, order, sku, cust, quantity=3)
    db.commit()

    resp = _get(client, owner_token, order.id)
    assert resp.status_code == 403


def test_next_pick_unknown_order_returns_404(client, courier_token):
    resp = _get(client, courier_token, 999999)
    assert resp.status_code == 404
