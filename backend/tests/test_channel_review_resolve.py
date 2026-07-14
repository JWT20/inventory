"""Tests for resolving needs_review channel orders (cancel / resume).

An active channel order that changed at the source while being picked lands in
`needs_review`. These endpoints let an admin unstick it: `cancel` frees the
remaining reservation and cancels it; `resume` puts it back on the pick list.
The reservation is still held while parked, so resume only flips the status.
"""
from app.models import (
    ChannelConnection,
    InventoryBalance,
    Order,
    OrderLine,
    Organization,
    SKU,
)
from tests.conftest import auth_header


def _org(db, slug):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _needs_review_order(db, org, *, reserved=2, booked=0, channel="shopify"):
    sku = SKU(sku_code="S1", name="Sok", organization_id=org.id,
              product_type="barcode", ean="8710000000001")
    db.add(sku)
    db.commit()
    db.refresh(sku)
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=reserved))
    order = Order(organization_id=org.id, channel=channel, external_id="RV1",
                  reference="SHO-RV1", status="needs_review")
    db.add(order)
    db.commit()
    db.add(OrderLine(order_id=order.id, sku_id=sku.id, quantity=2,
                     booked_count=booked, klant="Web"))
    db.commit()
    db.refresh(order)
    return order, sku


def test_cancel_releases_reservation_and_cancels(client, db, admin_token, monkeypatch):
    monkeypatch.setattr("app.routers.channels.push_inventory_to_shopify",
                        lambda *a, **k: None)
    org = _org(db, "rv-cancel")
    order, sku = _needs_review_order(db, org)

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    db.refresh(order)
    assert order.status == "cancelled"
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 0


def test_resume_reactivates_without_touching_reservation(client, db, admin_token):
    org = _org(db, "rv-resume")
    order, sku = _needs_review_order(db, org, reserved=2)

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "resume"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    db.refresh(order)
    assert order.status == "active"
    # Reservation was held the whole time — unchanged.
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2


def test_resolve_rejects_non_review_order(client, db, admin_token):
    org = _org(db, "rv-wrongstate")
    order, _ = _needs_review_order(db, org)
    order.status = "active"
    db.commit()

    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "cancel"},
    )
    assert resp.status_code == 400


def test_resolve_unknown_action_rejected(client, db, admin_token):
    org = _org(db, "rv-badaction")
    order, _ = _needs_review_order(db, org)
    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(admin_token),
        json={"action": "banaan"},
    )
    assert resp.status_code == 400


def test_resolve_requires_admin(client, db, courier_token):
    org = _org(db, "rv-noadmin")
    order, _ = _needs_review_order(db, org)
    resp = client.post(
        f"/api/channels/shopify/orders/{order.id}/resolve",
        headers=auth_header(courier_token),
        json={"action": "cancel"},
    )
    assert resp.status_code == 403
