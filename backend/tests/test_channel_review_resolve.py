"""Safety tests for resolving source-cancelled orders after picking began."""

from app.models import (
    Booking,
    InventoryBalance,
    Order,
    OrderLine,
    Organization,
    SKU,
    StockMovement,
)
from tests.conftest import auth_header


def _org(db, slug):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _needs_review_order(
    db,
    org,
    *,
    scanned_by,
    reserved=1,
    booked=1,
    review_reason="cancelled_after_pick",
    channel="shopify",
):
    sku = SKU(
        sku_code="S1",
        name="Sok",
        organization_id=org.id,
        product_type="barcode",
        ean="8710000000001",
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=org.id,
            quantity_on_hand=9,
            quantity_reserved=reserved,
        )
    )
    order = Order(
        organization_id=org.id,
        channel=channel,
        external_id="RV1",
        reference=f"SHO-{org.slug}",
        status="needs_review",
        review_reason=review_reason,
    )
    db.add(order)
    db.flush()
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        quantity=2,
        booked_count=booked,
        klant="Web",
    )
    db.add(line)
    db.flush()
    for _ in range(booked):
        db.add(
            Booking(
                order_id=order.id,
                order_line_id=line.id,
                sku_id=sku.id,
                scanned_by=scanned_by,
            )
        )
    db.commit()
    db.refresh(order)
    return order, line, sku


def test_cancel_without_restock_keeps_picked_stock_out(
    client, db, admin_token, admin_user, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.channels.push_inventory_to_channels", lambda *a, **k: None
    )
    org = _org(db, "rv-no-restock")
    order, line, sku = _needs_review_order(db, org, scanned_by=admin_user.id)

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel_without_restock"},
    )

    assert resp.status_code == 200
    db.refresh(order)
    db.refresh(line)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert order.status == "cancelled"
    assert order.review_reason is None
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (9, 0)
    assert line.booked_count == 1
    assert db.query(Booking).filter_by(order_id=order.id).count() == 1


def test_cancel_with_restock_returns_picked_stock_and_drops_bookings(
    client, db, admin_token, admin_user, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.channels.push_inventory_to_channels", lambda *a, **k: None
    )
    org = _org(db, "rv-restock")
    order, line, sku = _needs_review_order(db, org, scanned_by=admin_user.id)

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel_restock"},
    )

    assert resp.status_code == 200
    db.refresh(order)
    db.refresh(line)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    movement = db.query(StockMovement).filter_by(reference_id=order.id).one()
    assert order.status == "cancelled"
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (10, 0)
    assert line.booked_count == 0
    assert db.query(Booking).filter_by(order_id=order.id).count() == 0
    assert (movement.quantity, movement.movement_type) == (1, "pick")


def test_resolve_rejects_non_review_order(client, db, admin_token, admin_user):
    org = _org(db, "rv-wrongstate")
    order, _, _ = _needs_review_order(db, org, scanned_by=admin_user.id)
    order.status = "active"
    db.commit()

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel_without_restock"},
    )
    assert resp.status_code == 400


def test_resolve_rejects_non_cancellation_review(
    client, db, admin_token, admin_user
):
    org = _org(db, "rv-partial")
    order, _, _ = _needs_review_order(
        db,
        org,
        scanned_by=admin_user.id,
        review_reason="partially_fulfilled",
    )
    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel_without_restock"},
    )
    assert resp.status_code == 400


def test_resume_and_unknown_action_are_validation_errors(
    client, db, admin_token, admin_user
):
    org = _org(db, "rv-badaction")
    order, _, _ = _needs_review_order(db, org, scanned_by=admin_user.id)
    for action in ("resume", "banaan"):
        resp = client.post(
            f"/api/channels/shopify/orders/{order.id}/resolve",
            headers=auth_header(admin_token),
            json={"action": action},
        )
        assert resp.status_code == 422


def test_resolve_requires_admin(
    client, db, courier_token, admin_user
):
    org = _org(db, "rv-noadmin")
    order, _, _ = _needs_review_order(db, org, scanned_by=admin_user.id)
    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(courier_token),
        json={"action": "cancel_without_restock"},
    )
    assert resp.status_code == 403
