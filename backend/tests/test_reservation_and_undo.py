"""Review-fix regression tests: reservation lifecycle (#1/#2) and undo (#4),
courier label-recovery list (#3), and Shopify cache invalidation (#6)."""
from app.auth import create_token, hash_password
from app.models import (
    Customer,
    InventoryBalance,
    Order,
    OrderLine,
    Organization,
    SKU,
    User,
)
from tests.conftest import auth_header


def _org(db, slug):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _sku(db, org, code, ean, item_id=None):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean, shopify_inventory_item_id=item_id)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _balance(db, sku, org, on_hand, reserved):
    bal = InventoryBalance(sku_id=sku.id, organization_id=org.id,
                           quantity_on_hand=on_hand, quantity_reserved=reserved)
    db.add(bal)
    db.commit()
    return bal


def _reload_balance(db, sku, org):
    return (
        db.query(InventoryBalance)
        .filter(InventoryBalance.sku_id == sku.id,
                InventoryBalance.organization_id == org.id)
        .first()
    )


def _order(db, org, sku, channel, quantity=2, status="active"):
    customer = Customer(name="Klant", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(organization_id=org.id, reference=f"O-{org.slug}-{sku.sku_code}",
                  status=status, channel=channel,
                  external_id=(None if channel == "manual" else f"X-{org.slug}-{sku.sku_code}"),
                  channel_reference=(None if channel == "manual" else "1262"))
    db.add(order)
    db.flush()
    line = OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                     klant=customer.name, quantity=quantity,
                     booked_count=(quantity if status == "completed" else 0),
                     delivery_day="wednesday")
    db.add(line)
    db.commit()
    db.refresh(order)
    return order


def _scan(client, token, order_id, ean):
    return client.post("/api/picking/scan-ean",
                       json={"order_id": order_id, "ean": ean},
                       headers=auth_header(token))


def _undo(client, token, booking_id):
    return client.post("/api/picking/undo", json={"booking_id": booking_id},
                       headers=auth_header(token))


def test_fully_reserved_channel_order_is_pickable(client, db, courier_token):
    # #1: available == 0 (on_hand == reserved) must still be pickable on a channel
    # order — the reservation for the unit is released before the stock check.
    org = _org(db, "res-full")
    sku = _sku(db, org, "SOK-1", "8712000000015")
    order = _order(db, org, sku, channel="shopify")
    _balance(db, sku, org, on_hand=2, reserved=2)

    resp = _scan(client, courier_token, order.id, "8712000000015")
    assert resp.status_code == 200
    bal = _reload_balance(db, sku, org)
    assert bal.quantity_on_hand == 1
    assert bal.quantity_reserved == 1  # released together with the pick


def test_manual_pick_leaves_reservation_untouched(client, db, courier_token):
    # #2: a manual order never reserved, so picking it must not release a
    # reservation that belongs to a channel order (which would enable oversell).
    org = _org(db, "res-manual")
    sku = _sku(db, org, "SOK-2", "8712000000022")
    order = _order(db, org, sku, channel="manual")
    _balance(db, sku, org, on_hand=5, reserved=3)

    resp = _scan(client, courier_token, order.id, "8712000000022")
    assert resp.status_code == 200
    bal = _reload_balance(db, sku, org)
    assert bal.quantity_on_hand == 4  # one picked
    assert bal.quantity_reserved == 3  # untouched


def test_manual_undo_leaves_reservation_untouched(client, db, courier_token):
    # #2 (undo side): undoing a manual pick must not inflate the reservation.
    org = _org(db, "res-manual-undo")
    sku = _sku(db, org, "SOK-3", "8712000000039")
    order = _order(db, org, sku, channel="manual")
    _balance(db, sku, org, on_hand=5, reserved=3)

    booking_id = _scan(client, courier_token, order.id, "8712000000039").json()["booking_id"]
    assert _undo(client, courier_token, booking_id).status_code == 200
    bal = _reload_balance(db, sku, org)
    assert bal.quantity_on_hand == 5  # restocked
    assert bal.quantity_reserved == 3  # untouched


def test_undo_twice_is_rejected(client, db, courier_token):
    # #4: the second undo of the same booking must fail instead of restocking again.
    org = _org(db, "undo-twice")
    sku = _sku(db, org, "SOK-4", "8712000000046")
    order = _order(db, org, sku, channel="manual")
    _balance(db, sku, org, on_hand=5, reserved=0)

    booking_id = _scan(client, courier_token, order.id, "8712000000046").json()["booking_id"]
    assert _undo(client, courier_token, booking_id).status_code == 200
    # Second undo of the same unit is rejected (router 404 sequentially; the
    # service FOR UPDATE lock covers the concurrent race) — never a 2nd restock.
    assert _undo(client, courier_token, booking_id).status_code in (404, 409)
    bal = _reload_balance(db, sku, org)
    assert bal.quantity_on_hand == 5  # restocked exactly once


def test_undo_rejected_after_shopify_matched_local_fulfillment(
    client, db, courier_token
):
    org = _org(db, "undo-source-fulfilled")
    sku = _sku(db, org, "SOK-F", "8712000000091")
    order = _order(db, org, sku, channel="shopify")
    _balance(db, sku, org, on_hand=5, reserved=2)

    booking_id = _scan(client, courier_token, order.id, sku.ean).json()["booking_id"]
    line = order.lines[0]
    line.channel_fulfilled_from_app = 1
    db.commit()

    resp = _undo(client, courier_token, booking_id)
    assert resp.status_code == 409
    bal = _reload_balance(db, sku, org)
    assert (bal.quantity_on_hand, bal.quantity_reserved) == (4, 1)


def test_courier_list_includes_completed_channel_barcode(client, db, courier_token):
    # #3: a completed channel barcode order stays in the courier worklist so the
    # label step is reachable after a refresh.
    org = _org(db, "label-recover")
    sku = _sku(db, org, "SOK-5", "8712000000053")
    order = _order(db, org, sku, channel="shopify", status="completed")

    resp = client.get("/api/orders", headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert order.id in [o["id"] for o in resp.json()]


def test_courier_list_excludes_completed_manual(client, db, courier_token):
    # #3 regression: a completed *manual* order is terminal (no label) and must
    # not enter the default courier worklist, or a burst of them would fill the
    # limit=100 window and hide active/te-verzenden orders.
    org = _org(db, "no-manual-completed")
    sku = _sku(db, org, "SOK-7", "8712000000077")
    order = _order(db, org, sku, channel="manual", status="completed")

    resp = client.get("/api/orders", headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert order.id not in [o["id"] for o in resp.json()]


def test_barcode_line_counts_as_has_image(client, db, courier_token):
    # A barcode product needs no reference photo, so its order line reports
    # has_image=True — otherwise the order lands in the "Wacht op foto's" bucket.
    org = _org(db, "barcode-hasimg")
    sku = _sku(db, org, "SOK-8", "8712000000084")
    order = _order(db, org, sku, channel="shopify")

    resp = client.get(f"/api/orders/{order.id}", headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert resp.json()["lines"][0]["has_image"] is True


def test_ean_change_clears_channel_product_caches(client, db):
    # Changing the EAN invalidates channel ids resolved via the old EAN.
    org = _org(db, "ean-cache")
    # New EAN must be a valid EAN-13 (update_sku validates the checkdigit).
    sku = _sku(db, org, "SOK-6", "1234567890128", item_id="gid://old/123")
    sku.bol_offer_id = "old-bol-offer"
    owner = User(username="ec-owner", email="ec-owner@local",
                 hashed_password=hash_password("OwnerPass1!"), role="owner",
                 organization_id=org.id, is_verified=True)
    db.add(owner)
    db.commit()
    token = create_token(owner.id)

    resp = client.patch(f"/api/skus/{sku.id}", json={"ean": "4006381333931"},
                        headers=auth_header(token))
    assert resp.status_code == 200
    db.refresh(sku)
    assert sku.ean == "4006381333931"
    assert sku.shopify_inventory_item_id is None
    assert sku.bol_offer_id is None
