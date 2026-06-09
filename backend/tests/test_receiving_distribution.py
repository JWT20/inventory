"""Tests for the read-only SKU distribution (verdeel-lijst) endpoint."""

from app.models import Customer, InventoryBalance, Order, OrderLine, Organization, SKU
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


def test_distribution_scoped_to_context_week(client, db, courier_token, sample_org):
    sku = _make_sku(db)
    this_week = _make_customer(db, sample_org, "ThisWeek")
    next_week = _make_customer(db, sample_org, "NextWeek")
    context_order = _make_order(db, sample_org, "CTX", week="2026-W21")
    other_order = _make_order(db, sample_org, "NEXT", week="2026-W22")
    _make_line(db, context_order, sku, this_week, quantity=3)
    _make_line(db, other_order, sku, next_week, quantity=3)
    _set_stock(db, sku, sample_org, 10)
    db.commit()

    resp = _get(client, courier_token, context_order.id, sku.id)
    assert resp.status_code == 200
    # Only the context week's customer is listed; next week's order is excluded.
    assert [line["customer_name"] for line in resp.json()["lines"]] == ["ThisWeek"]


def test_distribution_total_remaining_bounded_by_stock(client, db, courier_token, sample_org):
    sku = _make_sku(db)
    wed_cust = _make_customer(db, sample_org, "Wed")
    thu_cust = _make_customer(db, sample_org, "Thu")
    context_order = _make_order(db, sample_org, "WED", week="2026-W21")
    thu_order = _make_order(db, sample_org, "THU", week="2026-W21")
    _make_line(db, context_order, sku, wed_cust, quantity=5, delivery_day="wednesday")
    _make_line(db, thu_order, sku, thu_cust, quantity=5, delivery_day="thursday")
    _set_stock(db, sku, sample_org, 4)  # one shared pool across both days
    db.commit()

    resp = _get(client, courier_token, context_order.id, sku.id)
    assert resp.status_code == 200
    body = resp.json()
    # Each day caps independently against the same 4 boxes, so raw caps sum to 8;
    # the headline total must be bounded by the 4 boxes physically on hand.
    assert body["total_remaining"] == 4


def test_distribution_rejects_other_org_for_owner(client, db, owner_token, sample_org):
    other_org = Organization(name="Andere Wijnhandel", slug="andere")
    db.add(other_org)
    db.flush()
    sku = _make_sku(db)
    cust = _make_customer(db, other_org, "Foreign")
    other_order = _make_order(db, other_org, "FOREIGN")
    _make_line(db, other_order, sku, cust, quantity=3)
    _set_stock(db, sku, other_org, 5)
    db.commit()

    # owner_token belongs to sample_org, not other_org.
    resp = _get(client, owner_token, other_order.id, sku.id)
    assert resp.status_code == 403


def test_distribution_rows_never_overpromise_stock(client, db, courier_token, sample_org):
    """Rows, not just the headline, are bounded by stock (no nog 4 + nog 4 on 4 boxes)."""
    sku = _make_sku(db)
    wed_cust = _make_customer(db, sample_org, "Wed")
    thu_cust = _make_customer(db, sample_org, "Thu")
    context_order = _make_order(db, sample_org, "WED", week="2026-W21")
    thu_order = _make_order(db, sample_org, "THU", week="2026-W21")
    _make_line(db, context_order, sku, wed_cust, quantity=5, delivery_day="wednesday")
    _make_line(db, thu_order, sku, thu_cust, quantity=5, delivery_day="thursday")
    _set_stock(db, sku, sample_org, 4)  # one shared pool across both days
    db.commit()

    body = _get(client, courier_token, context_order.id, sku.id).json()
    by_name = {line["customer_name"]: line for line in body["lines"]}
    # The 4 boxes go to the context order's day first; nothing is left to overpromise Thu.
    assert by_name["Wed"]["remaining_quantity"] == 4
    assert by_name["Thu"]["remaining_quantity"] == 0
    # Rows now sum to exactly what is on hand — never more than the headline total.
    assert sum(line["remaining_quantity"] for line in body["lines"]) == body["total_remaining"] == 4


def test_distribution_hides_sku_from_another_org(client, db, owner_token, sample_org):
    """A foreign tenant's org-scoped SKU must not leak its code/name — return 404."""
    other_org = Organization(name="Andere Wijnhandel", slug="andere2")
    db.add(other_org)
    db.flush()
    foreign_sku = SKU(sku_code="SECRET-1", name="Geheime Wijn", organization_id=other_org.id)
    db.add(foreign_sku)
    db.flush()
    my_order = _make_order(db, sample_org, "MINE")
    db.commit()

    # owner_token's own order, but a foreign org's sku_id — existence must not be revealed.
    resp = _get(client, owner_token, my_order.id, foreign_sku.id)
    assert resp.status_code == 404


def test_distribution_unknown_order_returns_404(client, courier_token):
    resp = _get(client, courier_token, 999999, 1)
    assert resp.status_code == 404
