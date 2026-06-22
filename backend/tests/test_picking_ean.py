"""Tests for the barcode/EAN picking endpoint (Fase 1, PR-C).

POST /api/picking/scan-ean books one unit on a barcode order by scanned EAN.
It is gated on the order's barcode_picking module (keyed on the order's org,
not the courier's) and resolves the EAN within that org.
"""
from app.auth import create_token, hash_password
from app.models import Customer, InventoryBalance, Order, OrderLine, Organization, SKU, User
from tests.conftest import auth_header


def _make_org(db, slug, modules):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_barcode_sku(db, org, code, ean):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _make_active_order(db, org, sku, quantity=2, booked=0):
    customer = Customer(name="Kanaalklant", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(organization_id=org.id, reference=f"EAN-{sku.sku_code}",
                  status="active", channel="shopify", external_id=f"X-{sku.sku_code}")
    db.add(order)
    db.flush()
    line = OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                     klant=customer.name, quantity=quantity, booked_count=booked,
                     delivery_day="wednesday")
    db.add(line)
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    db.refresh(order)
    db.refresh(line)
    return order, line


def _scan(client, token, order_id, ean):
    return client.post(
        "/api/picking/scan-ean",
        json={"order_id": order_id, "ean": ean},
        headers=auth_header(token),
    )


def _barcode_org(db, slug="socks-pick"):
    return _make_org(db, slug, ["inventory", "orders", "barcode_picking", "channel_orders"])


def test_scan_books_one_unit(client, db, courier_token):
    org = _barcode_org(db)
    sku = _make_barcode_sku(db, org, "SOK-1", "8711111111111")
    order, line = _make_active_order(db, org, sku, quantity=2)

    resp = _scan(client, courier_token, order.id, "8711111111111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sku_code"] == "SOK-1"
    assert data["booked_quantity"] == 1
    assert data["remaining_quantity"] == 1
    assert data["order_completed"] is False

    db.refresh(line)
    assert line.booked_count == 1


def test_scan_completes_order_on_last_unit(client, db, courier_token):
    org = _barcode_org(db, "socks-done")
    sku = _make_barcode_sku(db, org, "SOK-2", "8722222222222")
    order, _line = _make_active_order(db, org, sku, quantity=1)

    resp = _scan(client, courier_token, order.id, "8722222222222")
    assert resp.status_code == 200
    assert resp.json()["order_completed"] is True


def test_unknown_ean_returns_404(client, db, courier_token):
    org = _barcode_org(db, "socks-unknown")
    sku = _make_barcode_sku(db, org, "SOK-3", "8733333333333")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "0000000000000")
    assert resp.status_code == 404


def test_ean_from_other_org_not_found(client, db, courier_token):
    """An EAN that exists, but in another organization, must not resolve here."""
    org = _barcode_org(db, "socks-a")
    other = _barcode_org(db, "socks-b")
    sku = _make_barcode_sku(db, org, "SOK-4", "8744444444444")
    # Same EAN string, different org — legitimate per uq_skus_org_ean.
    _make_barcode_sku(db, other, "SOK-4B", "8744444444444")
    order, _ = _make_active_order(db, org, sku)

    # The order is in `org`; scanning resolves within org and finds SOK-4. Now
    # an order in `other` scanning its own product is fine — but a product that
    # only exists in `other` must be invisible to an `org` order:
    foreign = _make_barcode_sku(db, other, "ONLY-B", "8755555555555")
    resp = _scan(client, courier_token, order.id, "8755555555555")
    assert resp.status_code == 404


def test_product_not_on_order_returns_400(client, db, courier_token):
    org = _barcode_org(db, "socks-noton")
    sku = _make_barcode_sku(db, org, "SOK-5", "8766666666666")
    other_sku = _make_barcode_sku(db, org, "SOK-5B", "8777777777777")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "8777777777777")
    assert resp.status_code == 400  # exists in org, but not on this order


def test_already_complete_returns_409(client, db, courier_token):
    org = _barcode_org(db, "socks-complete")
    sku = _make_barcode_sku(db, org, "SOK-6", "8788888888888")
    order, _ = _make_active_order(db, org, sku, quantity=1, booked=1)

    resp = _scan(client, courier_token, order.id, "8788888888888")
    assert resp.status_code == 409


def test_blocked_when_org_lacks_barcode_module(client, db, courier_token):
    """A vision org's order must not be EAN-pickable, even if a product has an EAN."""
    org = _make_org(db, "wine-novbarcode", ["inventory", "orders", "vision_picking"])
    sku = _make_barcode_sku(db, org, "WINE-EAN", "8799999999999")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "8799999999999")
    assert resp.status_code == 403


def test_barcode_order_exposes_pick_method_and_surfaces_weekless(client, db, courier_token):
    """A weekless born-active barcode order is pick_method='barcode' and shows
    in the courier list even when a week is selected (it has no week)."""
    org = _barcode_org(db, "socks-list")
    sku = _make_barcode_sku(db, org, "SOK-8", "8700000000002")
    order, _ = _make_active_order(db, org, sku)  # weekless, active

    resp = client.get("/api/orders?week=2026-W21", headers=auth_header(courier_token))
    assert resp.status_code == 200
    by_id = {o["id"]: o for o in resp.json()}
    assert order.id in by_id
    assert by_id[order.id]["pick_method"] == "barcode"


def test_inactive_order_rejected(client, db, courier_token):
    org = _barcode_org(db, "socks-inactive")
    sku = _make_barcode_sku(db, org, "SOK-7", "8700000000001")
    order, _ = _make_active_order(db, org, sku)
    order.status = "pending_approval"
    db.commit()

    resp = _scan(client, courier_token, order.id, "8700000000001")
    assert resp.status_code == 400
