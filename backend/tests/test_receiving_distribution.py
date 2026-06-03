"""Tests for the read-only SKU distribution (verdeel-lijst) endpoint."""

from app.models import Customer, InventoryBalance, Order, OrderLine, SKU
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


def _make_order(db, org, ref, week="2026-W21"):
    order = Order(
        organization_id=org.id,
        reference=ref,
        status="active",
        delivery_week=week,
    )
    db.add(order)
    db.flush()
    return order


def _make_line(db, order, sku, customer, quantity, booked_count=0, delivery_day="wednesday"):
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=quantity,
        booked_count=booked_count,
        delivery_day=delivery_day,
    )
    db.add(line)
    db.flush()
    return line


def _set_stock(db, sku, org, quantity):
    db.add(InventoryBalance(
        sku_id=sku.id,
        organization_id=org.id,
        quantity_on_hand=quantity,
        quantity_reserved=0,
    ))
    db.flush()


def _get(client, token, order_id, sku_id):
    return client.get(
        f"/api/receiving/distribution?order_id={order_id}&sku_id={sku_id}",
        headers=auth_header(token),
    )


def test_distribution_lists_all_customers_including_completed(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust_a = _make_customer(db, sample_org, "Alpha")
    cust_b = _make_customer(db, sample_org, "Bravo")
    cust_c = _make_customer(db, sample_org, "Charlie")
    context_order = _make_order(db, sample_org, "CTX")
    order_b = _make_order(db, sample_org, "B")
    order_c = _make_order(db, sample_org, "C")
    _make_line(db, context_order, sku, cust_a, quantity=5, booked_count=2)
    _make_line(db, order_b, sku, cust_b, quantity=4, booked_count=4)  # klaar
    _make_line(db, order_c, sku, cust_c, quantity=6, booked_count=0)
    _set_stock(db, sku, sample_org, 50)  # plenty → caps == raw remaining
    db.commit()

    resp = _get(client, courier_token, context_order.id, sku.id)
    assert resp.status_code == 200
    body = resp.json()

    assert body["sku_code"] == sku.sku_code
    assert body["scope"] == "de open orders"
    assert body["total_remaining"] == 3 + 0 + 6
    assert len(body["lines"]) == 3

    # Context order line comes first.
    assert body["lines"][0]["customer_name"] == "Alpha"
    assert body["lines"][0]["is_context_order"] is True
    assert body["lines"][0]["rolcontainer"] == "KLANT ALPHA"
    assert body["lines"][0]["remaining_quantity"] == 3

    by_name = {line["customer_name"]: line for line in body["lines"]}
    # Fully-booked customer is shown, not hidden, and flagged complete.
    assert by_name["Bravo"]["is_complete"] is True
    assert by_name["Bravo"]["remaining_quantity"] == 0
    assert by_name["Charlie"]["is_complete"] is False
    assert by_name["Charlie"]["remaining_quantity"] == 6


def test_distribution_remaining_uses_fair_allocation_cap(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    big = _make_customer(db, sample_org, "Big")
    small = _make_customer(db, sample_org, "Small")
    context_order = _make_order(db, sample_org, "CTX")
    order_small = _make_order(db, sample_org, "SMALL")
    _make_line(db, context_order, sku, big, quantity=8)
    _make_line(db, order_small, sku, small, quantity=2)
    _set_stock(db, sku, sample_org, 1)  # scarcity: only 1 box to give
    db.commit()

    resp = _get(client, courier_token, context_order.id, sku.id)
    assert resp.status_code == 200
    by_name = {line["customer_name"]: line for line in resp.json()["lines"]}

    # Fair cap (smallest-first) gives the single box to Small, not raw remaining.
    assert by_name["Small"]["remaining_quantity"] == 1
    assert by_name["Big"]["remaining_quantity"] == 0
    assert by_name["Big"]["is_complete"] is False


def test_distribution_adhoc_order_scoped_to_itself(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    adhoc_cust = _make_customer(db, sample_org, "AdHoc")
    weekly_cust = _make_customer(db, sample_org, "Weekly")
    adhoc_order = _make_order(db, sample_org, "ADHOC", week=None)
    weekly_order = _make_order(db, sample_org, "WEEKLY", week="2026-W21")
    _make_line(db, adhoc_order, sku, adhoc_cust, quantity=3)
    _make_line(db, weekly_order, sku, weekly_cust, quantity=3)
    _set_stock(db, sku, sample_org, 10)
    db.commit()

    resp = _get(client, courier_token, adhoc_order.id, sku.id)
    assert resp.status_code == 200
    body = resp.json()

    assert body["scope"] == "deze order"
    assert [line["customer_name"] for line in body["lines"]] == ["AdHoc"]


def test_distribution_unknown_order_returns_404(client, courier_token):
    resp = _get(client, courier_token, 999999, 1)
    assert resp.status_code == 404
