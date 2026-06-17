"""Tests for the next-pick suggestion endpoint used by Scan & Boek."""

from app.models import Customer, Order, OrderLine, Organization, SKU
from tests.conftest import auth_header


def _make_sku(db, code="SKU-1", is_bottle=False):
    sku = SKU(sku_code=code, name=f"Wine {code}", is_bottle=is_bottle)
    db.add(sku)
    db.flush()
    return sku


def _get_mode(client, token, order_id, scan_mode):
    return client.get(
        f"/api/orders/{order_id}/next-pick?scan_mode={scan_mode}",
        headers=auth_header(token),
    )


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


def test_next_pick_other_order_spans_weeks_but_not_orgs(
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
    # Another week of the same org is now a valid suggestion (matches the
    # booking sweep, which spans all weeks for scheduled orders).
    next_week = _make_order(db, sample_org, "NEXT", week="2026-W22")
    next_line = _make_line(db, next_week, sku, cust, quantity=2)
    # Another org stays excluded.
    foreign = _make_order(db, other_org, "FOREIGN", week="2026-W21")
    _make_line(db, foreign, sku, foreign_cust, quantity=2)
    db.commit()

    resp = _get(client, courier_token, full_order.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "other_order"
    assert body["order_id"] == next_week.id
    assert body["order_line_id"] == next_line.id


def test_next_pick_skips_adhoc_orders_in_fallback(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    full_order = _make_order(db, sample_org, "FULL", week="2026-W21")
    _make_line(db, full_order, sku, cust, quantity=1, booked_count=1)
    # Ad-hoc order (no week) is never swept into weekly matching, so it must
    # not be suggested either — the box would not be bookable against it.
    adhoc = _make_order(db, sample_org, "ADHOC", week=None)
    _make_line(db, adhoc, sku, cust, quantity=2)
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


def test_next_pick_prefers_earlier_week_over_context_order(
    client, db, courier_token, sample_org
):
    """book_box books week FIFO, so an earlier week is suggested first even
    when the context order still has open lines — the card must agree with
    where the scan actually lands."""
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    early = _make_order(db, sample_org, "EARLY", week="2026-W20")
    early_line = _make_line(db, early, sku, cust, quantity=2)
    context = _make_order(db, sample_org, "CTX", week="2026-W21")
    _make_line(db, context, sku, cust, quantity=2)
    db.commit()

    resp = _get(client, courier_token, context.id)
    assert resp.status_code == 200
    body = resp.json()
    # Earlier week wins; labelled other_order, not "in deze order".
    assert body["source"] == "other_order"
    assert body["order_id"] == early.id
    assert body["order_line_id"] == early_line.id


def test_next_pick_context_order_first_within_same_week(
    client, db, courier_token, sample_org
):
    """Within one week the started/context order is preferred, matching the
    receiving tiebreak — so it is labelled this_order."""
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    # Other order created first (lower id) but context must still win.
    other = _make_order(db, sample_org, "OTHER", week="2026-W21")
    _make_line(db, other, sku, cust, quantity=2)
    context = _make_order(db, sample_org, "CTX", week="2026-W21")
    context_line = _make_line(db, context, sku, cust, quantity=2)
    db.commit()

    resp = _get(client, courier_token, context.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "this_order"
    assert body["order_id"] == context.id
    assert body["order_line_id"] == context_line.id


def test_next_pick_respects_scan_mode(client, db, courier_token, sample_org):
    box_sku = _make_sku(db, "BOX", is_bottle=False)
    bottle_sku = _make_sku(db, "BOTTLE", is_bottle=True)
    cust = _make_customer(db, sample_org, "Alpha")
    order = _make_order(db, sample_org, "CTX")
    box_line = _make_line(db, order, box_sku, cust, quantity=2)
    bottle_line = _make_line(db, order, bottle_sku, cust, quantity=2)
    db.commit()

    box = _get_mode(client, courier_token, order.id, "box").json()
    assert box["sku_id"] == box_sku.id
    assert box["order_line_id"] == box_line.id

    bottle = _get_mode(client, courier_token, order.id, "bottle").json()
    assert bottle["sku_id"] == bottle_sku.id
    assert bottle["order_line_id"] == bottle_line.id


def test_next_pick_no_match_for_scan_mode_returns_null(
    client, db, courier_token, sample_org
):
    box_sku = _make_sku(db, "BOX", is_bottle=False)
    cust = _make_customer(db, sample_org, "Alpha")
    order = _make_order(db, sample_org, "CTX")
    _make_line(db, order, box_sku, cust, quantity=2)
    db.commit()

    # Only a box line is open, so bottle mode has nothing to suggest.
    resp = _get_mode(client, courier_token, order.id, "bottle")
    assert resp.status_code == 200
    assert resp.json() is None


def test_next_pick_full_adhoc_order_does_not_fall_back(
    client, db, courier_token, sample_org
):
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    # Completed ad-hoc order (no week) must not switch context to a scheduled
    # order — receiving scopes ad-hoc scans to themselves.
    adhoc = _make_order(db, sample_org, "ADHOC", week=None)
    _make_line(db, adhoc, sku, cust, quantity=1, booked_count=1)
    scheduled = _make_order(db, sample_org, "SCHED", week="2026-W21")
    _make_line(db, scheduled, sku, cust, quantity=3)
    db.commit()

    resp = _get(client, courier_token, adhoc.id)
    assert resp.status_code == 200
    assert resp.json() is None


def test_next_pick_rejects_customer_role(client, db, customer_token, sample_org):
    sku = _make_sku(db)
    cust = _make_customer(db, sample_org, "Alpha")
    order = _make_order(db, sample_org, "CTX")
    _make_line(db, order, sku, cust, quantity=3)
    db.commit()

    # Customer belongs to the org but has no business in the pick flow.
    resp = _get(client, customer_token, order.id)
    assert resp.status_code == 403


def test_next_pick_unknown_order_returns_404(client, courier_token):
    resp = _get(client, courier_token, 999999)
    assert resp.status_code == 404
