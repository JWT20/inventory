"""Tests for week-scoped receiving line selection."""

from app.models import Booking, Customer, InventoryBalance, Order, OrderLine, SKU
from app.routers.receiving import _register_signer, _select_order_line_for_scope, _signer
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
    balance = InventoryBalance(
        sku_id=sku.id,
        organization_id=org.id,
        quantity_on_hand=quantity,
        quantity_reserved=0,
    )
    db.add(balance)
    db.flush()
    return balance


def test_week_scope_prefers_start_order_when_it_can_book(db, sample_org):
    sku = _make_sku(db)
    start_customer = _make_customer(db, sample_org, "Start")
    other_customer = _make_customer(db, sample_org, "Other")
    start_order = _make_order(db, sample_org, "START")
    other_order = _make_order(db, sample_org, "OTHER")
    start_line = _make_line(db, start_order, sku, start_customer, quantity=8)
    _make_line(db, other_order, sku, other_customer, quantity=2)
    _set_stock(db, sku, sample_org, 10)

    selected, cap_remaining = _select_order_line_for_scope(db, start_order, sku.id)

    assert selected.id == start_line.id
    assert cap_remaining == 8


def test_week_scope_falls_back_when_start_order_does_not_need_sku(db, sample_org):
    start_sku = _make_sku(db, "START-SKU")
    scanned_sku = _make_sku(db, "SCAN-SKU")
    start_customer = _make_customer(db, sample_org, "Start")
    other_customer = _make_customer(db, sample_org, "Other")
    start_order = _make_order(db, sample_org, "START")
    other_order = _make_order(db, sample_org, "OTHER")
    _make_line(db, start_order, start_sku, start_customer, quantity=3)
    fallback_line = _make_line(db, other_order, scanned_sku, other_customer, quantity=4)
    _set_stock(db, scanned_sku, sample_org, 4)

    selected, cap_remaining = _select_order_line_for_scope(db, start_order, scanned_sku.id)

    assert selected.id == fallback_line.id
    assert cap_remaining == 4


def test_week_scope_falls_back_when_start_order_cap_is_reached(db, sample_org):
    sku = _make_sku(db)
    start_customer = _make_customer(db, sample_org, "Start")
    other_customer = _make_customer(db, sample_org, "Other")
    start_order = _make_order(db, sample_org, "START")
    other_order = _make_order(db, sample_org, "OTHER")
    _make_line(db, start_order, sku, start_customer, quantity=8)
    fallback_line = _make_line(db, other_order, sku, other_customer, quantity=1)
    _set_stock(db, sku, sample_org, 1)

    selected, cap_remaining = _select_order_line_for_scope(db, start_order, sku.id)

    assert selected.id == fallback_line.id
    assert cap_remaining == 1


def test_week_scope_uses_delivery_day_caps_independently(db, sample_org):
    sku = _make_sku(db)
    start_customer = _make_customer(db, sample_org, "Start")
    other_customer = _make_customer(db, sample_org, "Other")
    start_order = _make_order(db, sample_org, "START")
    other_order = _make_order(db, sample_org, "OTHER")
    start_line = _make_line(
        db, start_order, sku, start_customer, quantity=2, delivery_day="thursday"
    )
    _make_line(db, other_order, sku, other_customer, quantity=2, delivery_day="wednesday")
    _set_stock(db, sku, sample_org, 1)

    selected, cap_remaining = _select_order_line_for_scope(db, start_order, sku.id)

    assert selected.id == start_line.id
    assert cap_remaining == 1


def test_scope_matches_open_line_in_another_week(db, sample_org):
    sku = _make_sku(db)
    this_week_customer = _make_customer(db, sample_org, "ThisWeek")
    next_week_customer = _make_customer(db, sample_org, "NextWeek")
    this_week_order = _make_order(db, sample_org, "THIS", week="2026-W21")
    next_week_order = _make_order(db, sample_org, "NEXT", week="2026-W22")
    # SKU is only open in next week's order — old behavior would reject this.
    next_week_line = _make_line(db, next_week_order, sku, next_week_customer, quantity=3)
    _set_stock(db, sku, sample_org, 3)

    selected, cap_remaining = _select_order_line_for_scope(db, this_week_order, sku.id)

    assert selected.id == next_week_line.id
    assert cap_remaining == 3


def test_scope_prefers_context_order_over_earlier_week(db, sample_org):
    sku = _make_sku(db)
    early_customer = _make_customer(db, sample_org, "Early")
    late_customer = _make_customer(db, sample_org, "Late")
    early_order = _make_order(db, sample_org, "EARLY", week="2026-W21")
    late_order = _make_order(db, sample_org, "LATE", week="2026-W22")
    _make_line(db, early_order, sku, early_customer, quantity=8)
    late_line = _make_line(db, late_order, sku, late_customer, quantity=8)
    _set_stock(db, sku, sample_org, 10)

    selected, cap_remaining = _select_order_line_for_scope(db, late_order, sku.id)

    assert selected.id == late_line.id
    # Cap is computed per week independently, so it is not split with the later week.
    assert cap_remaining == 8


def test_scope_falls_back_to_earliest_week_when_context_does_not_need_sku(
    db, sample_org
):
    context_sku = _make_sku(db, "CONTEXT")
    scanned_sku = _make_sku(db, "SCANNED")
    customer = _make_customer(db, sample_org, "Customer")
    context = _make_order(db, sample_org, "CTX", week="2026-W22")
    _make_line(db, context, context_sku, customer, quantity=1)
    late = _make_order(db, sample_org, "LATE", week="2026-W23")
    _make_line(db, late, scanned_sku, customer, quantity=2)
    early = _make_order(db, sample_org, "EARLY", week="2026-W20")
    early_line = _make_line(db, early, scanned_sku, customer, quantity=2)
    _set_stock(db, scanned_sku, sample_org, 4)

    selected, cap_remaining = _select_order_line_for_scope(db, context, scanned_sku.id)

    assert selected.id == early_line.id
    assert cap_remaining == 2


def test_scheduled_scan_skips_adhoc_null_week_orders(db, sample_org):
    sku = _make_sku(db)
    adhoc_customer = _make_customer(db, sample_org, "AdHoc")
    weekly_customer = _make_customer(db, sample_org, "Weekly")
    context_order = _make_order(db, sample_org, "CONTEXT", week="2026-W21")
    adhoc_order = _make_order(db, sample_org, "ADHOC", week=None)
    weekly_order = _make_order(db, sample_org, "WEEKLY", week="2026-W22")
    # Context order does not carry the SKU; an ad-hoc (null-week) order does, and so
    # does a real weekly order. The ad-hoc order must never be picked for a weekly scan.
    _make_line(db, adhoc_order, sku, adhoc_customer, quantity=5)
    weekly_line = _make_line(db, weekly_order, sku, weekly_customer, quantity=5)
    _set_stock(db, sku, sample_org, 5)

    selected, cap_remaining = _select_order_line_for_scope(db, context_order, sku.id)

    assert selected.id == weekly_line.id
    assert cap_remaining == 5


def test_confirm_books_exact_order_line_and_order_id(
    client, db, courier_token, courier_user, sample_org
):
    sku = _make_sku(db)
    start_customer = _make_customer(db, sample_org, "Start")
    fallback_customer = _make_customer(db, sample_org, "Fallback")
    start_order = _make_order(db, sample_org, "START")
    fallback_order = _make_order(db, sample_org, "FALLBACK")
    start_line = _make_line(db, start_order, sku, start_customer, quantity=3)
    fallback_line = _make_line(db, fallback_order, sku, fallback_customer, quantity=3)
    _set_stock(db, sku, sample_org, 3)
    db.commit()

    token = _signer.dumps({
        "context_order_id": start_order.id,
        "order_id": fallback_order.id,
        "order_line_id": fallback_line.id,
        "sku_id": sku.id,
        "confidence": 0.99,
        "scan_image_key": "scans/test.jpg",
        "user_id": courier_user.id,
    })

    resp = client.post(
        "/api/receiving/book/confirm",
        json={"confirmation_token": token, "quantity": 1},
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == fallback_order.id
    assert body["order_line_id"] == fallback_line.id
    db.refresh(start_line)
    db.refresh(fallback_line)
    assert start_line.booked_count == 0
    assert fallback_line.booked_count == 1
    booking = db.get(Booking, body["id"])
    assert booking.order_id == fallback_order.id
    assert booking.order_line_id == fallback_line.id
    # Partial pick (1 of 3) must not signal completion to the picker.
    assert body["order_completed"] is False


def test_confirm_signals_order_completed_on_last_unit(
    client, db, courier_token, courier_user, sample_org
):
    """Vision confirm exposes order_completed so the picker gets fireworks."""
    sku = _make_sku(db)
    customer = _make_customer(db, sample_org, "Solo")
    order = _make_order(db, sample_org, "SOLO")
    line = _make_line(db, order, sku, customer, quantity=1)
    _set_stock(db, sku, sample_org, 1)
    db.commit()

    token = _signer.dumps({
        "order_id": order.id,
        "order_line_id": line.id,
        "sku_id": sku.id,
        "confidence": 0.99,
        "scan_image_key": "scans/test.jpg",
        "user_id": courier_user.id,
    })

    resp = client.post(
        "/api/receiving/book/confirm",
        json={"confirmation_token": token, "quantity": 1},
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    assert resp.json()["order_completed"] is True


def test_book_more_uses_order_line_id(client, db, courier_token, sample_org):
    sku = _make_sku(db)
    start_customer = _make_customer(db, sample_org, "Start")
    fallback_customer = _make_customer(db, sample_org, "Fallback")
    start_order = _make_order(db, sample_org, "START")
    fallback_order = _make_order(db, sample_org, "FALLBACK")
    start_line = _make_line(db, start_order, sku, start_customer, quantity=3)
    fallback_line = _make_line(db, fallback_order, sku, fallback_customer, quantity=3)
    _set_stock(db, sku, sample_org, 5)
    db.commit()

    resp = client.post(
        "/api/receiving/book/more",
        data={
            "order_line_id": str(fallback_line.id),
            "quantity": "2",
            "scan_image_path": "scans/test.jpg",
        },
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == fallback_order.id
    assert body["order_line_id"] == fallback_line.id
    db.refresh(start_line)
    db.refresh(fallback_line)
    assert start_line.booked_count == 0
    assert fallback_line.booked_count == 2
    bookings = db.query(Booking).order_by(Booking.id).all()
    assert len(bookings) == 2
    assert {booking.order_id for booking in bookings} == {fallback_order.id}
    assert {booking.order_line_id for booking in bookings} == {fallback_line.id}


def test_register_reference_selects_week_scope_fallback_line(
    client, db, courier_token, courier_user, sample_org, monkeypatch
):
    class FakeStorage:
        def read(self, key):
            return b"image-bytes"

        def save(self, key, data):
            self.saved_key = key

        def url(self, key):
            return f"/uploads/{key}"

    async def fake_process_image(_image_bytes):
        return "reference description", [0.1] * 3072, True

    monkeypatch.setattr("app.routers.receiving.storage", FakeStorage())
    monkeypatch.setattr("app.routers.receiving.process_image", fake_process_image)

    start_sku = _make_sku(db, "START-SKU")
    fallback_sku = _make_sku(db, "FALLBACK-SKU")
    start_customer = _make_customer(db, sample_org, "Start")
    fallback_customer = _make_customer(db, sample_org, "Fallback")
    start_order = _make_order(db, sample_org, "START")
    fallback_order = _make_order(db, sample_org, "FALLBACK")
    _make_line(db, start_order, start_sku, start_customer, quantity=2)
    fallback_line = _make_line(db, fallback_order, fallback_sku, fallback_customer, quantity=2)
    _set_stock(db, fallback_sku, sample_org, 2)
    db.commit()

    register_token = _register_signer.dumps({
        "context_order_id": start_order.id,
        "scan_image_key": "scans/register.jpg",
        "user_id": courier_user.id,
    })

    resp = client.post(
        "/api/receiving/register-reference",
        json={"register_token": register_token, "sku_id": fallback_sku.id},
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["context_order_id"] == start_order.id
    assert body["order_id"] == fallback_order.id
    assert body["order_line_id"] == fallback_line.id

    confirm_data = _signer.loads(body["confirmation_token"])
    assert confirm_data["context_order_id"] == start_order.id
    assert confirm_data["order_id"] == fallback_order.id
    assert confirm_data["order_line_id"] == fallback_line.id
