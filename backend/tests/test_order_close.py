"""Tests for closing active orders that are not fully picked."""

from app.models import Customer, InventoryBalance, Order, OrderLine, SKU
from app.routers.receiving import _open_scope_lines_query
from tests.conftest import auth_header


def _make_order(
    db, org, ref, status="active", week="2026-W21", channel="manual"
):
    order = Order(
        organization_id=org.id,
        reference=ref,
        status=status,
        delivery_week=week,
        channel=channel,
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
    )
    db.add(line)
    db.flush()
    return line


def _setup_partial_order(
    db, sample_org, ref="PARTIAL", status="active", channel="manual"
):
    sku = SKU(sku_code=f"SKU-{ref}", name=f"Wine {ref}")
    db.add(sku)
    db.flush()
    customer = Customer(name=f"Cust {ref}", organization_id=sample_org.id)
    db.add(customer)
    db.flush()
    order = _make_order(db, sample_org, ref, status=status, channel=channel)
    line = _make_line(db, order, sku, customer, quantity=8, booked_count=3)
    db.commit()
    return order, line


def _set_balance(db, org, line, *, reserved):
    balance = InventoryBalance(
        sku_id=line.sku_id,
        organization_id=org.id,
        quantity_on_hand=10,
        quantity_reserved=reserved,
    )
    db.add(balance)
    db.commit()
    return balance


def test_owner_closes_partial_order_keeps_bookings(client, db, owner_token, sample_org):
    order, line = _setup_partial_order(db, sample_org)

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    db.refresh(order)
    db.refresh(line)
    assert order.status == "closed"
    # Ordered and booked quantities are left untouched.
    assert line.quantity == 8
    assert line.booked_count == 3


def test_closing_channel_order_releases_only_its_unbooked_reservation(
    client, db, owner_token, sample_org
):
    order, line = _setup_partial_order(
        db, sample_org, ref="CHANNEL", channel="shopify"
    )
    balance = _set_balance(db, sample_org, line, reserved=12)

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    db.refresh(balance)
    assert balance.quantity_on_hand == 10
    assert balance.quantity_reserved == 7


def test_closing_inactive_channel_order_does_not_release_reservation(
    client, db, owner_token, sample_org
):
    order, line = _setup_partial_order(
        db,
        sample_org,
        ref="CHANNEL-PENDING",
        status="pending_images",
        channel="shopify",
    )
    balance = _set_balance(db, sample_org, line, reserved=7)

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    db.refresh(balance)
    assert balance.quantity_reserved == 7


def test_closing_manual_order_does_not_release_other_reservations(
    client, db, owner_token, sample_org
):
    order, line = _setup_partial_order(db, sample_org, ref="MANUAL")
    balance = _set_balance(db, sample_org, line, reserved=5)

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    db.refresh(balance)
    assert balance.quantity_reserved == 5


def test_closed_order_leaves_scan_scope(client, db, owner_token, sample_org):
    order, _line = _setup_partial_order(db, sample_org, ref="SCOPE")
    # A separate active order acts as the scan context for the same week.
    context_order = _make_order(db, sample_org, "CONTEXT")
    db.commit()

    before = _open_scope_lines_query(db, context_order).all()
    assert any(l.order_id == order.id for l in before)

    client.post(f"/api/orders/{order.id}/close", headers=auth_header(owner_token))
    db.refresh(order)

    after = _open_scope_lines_query(db, context_order).all()
    assert not any(l.order_id == order.id for l in after)


def test_courier_can_close(client, db, courier_token, sample_org):
    order, _line = _setup_partial_order(db, sample_org, ref="COURIER")

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    db.refresh(order)
    assert order.status == "closed"


def test_customer_cannot_close(client, db, customer_token, sample_org):
    order, _line = _setup_partial_order(db, sample_org, ref="CUST")

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(customer_token),
    )

    assert resp.status_code == 403
    db.refresh(order)
    assert order.status == "active"


def test_courier_can_fetch_closed_order_detail_and_bookings(
    client, db, courier_token, sample_org
):
    order, _line = _setup_partial_order(db, sample_org, ref="HIST")
    client.post(f"/api/orders/{order.id}/close", headers=auth_header(courier_token))

    detail = client.get(f"/api/orders/{order.id}", headers=auth_header(courier_token))
    assert detail.status_code == 200
    assert detail.json()["status"] == "closed"

    bookings = client.get(
        f"/api/orders/{order.id}/bookings", headers=auth_header(courier_token)
    )
    assert bookings.status_code == 200


def test_owner_can_close_pending_images_order(client, db, owner_token, sample_org):
    order, _line = _setup_partial_order(
        db, sample_org, ref="PENDIMG", status="pending_images"
    )

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    db.refresh(order)
    assert order.status == "closed"


def test_courier_can_close_pending_images_order(client, db, courier_token, sample_org):
    order, _line = _setup_partial_order(
        db, sample_org, ref="PENDIMGC", status="pending_images"
    )

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 200
    db.refresh(order)
    assert order.status == "closed"


def test_cannot_close_non_active_order(client, db, owner_token, sample_org):
    order, _line = _setup_partial_order(db, sample_org, ref="DONE", status="completed")

    resp = client.post(
        f"/api/orders/{order.id}/close",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 400
    db.refresh(order)
    assert order.status == "completed"
