"""Tests for the location-driven barcode pick flow.

POST /api/picking/scan-location verifies a shelf and lists its order products;
POST /api/picking/scan-ean enforces that a located product is only booked from
its own shelf. Products without a location skip the gate (backward compatible).
"""
from app.models import (
    Customer,
    InventoryBalance,
    Location,
    Order,
    OrderLine,
    Organization,
    SKU,
    SKULocation,
)
from tests.conftest import auth_header


def _org(db, slug="pl-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _sku(db, org, code, ean):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _location(db, code, *skus):
    loc = Location(code=code)
    db.add(loc)
    db.flush()
    for sku in skus:
        db.add(SKULocation(location_id=loc.id, sku_id=sku.id))
    db.commit()
    db.refresh(loc)
    return loc


def _order(db, org, *skus, quantity=2):
    customer = Customer(name="Kanaalklant", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(organization_id=org.id, reference=f"PL-{org.slug}",
                  status="active", channel="shopify", external_id=f"X-{org.slug}",
                  channel_reference="1262")
    db.add(order)
    db.flush()
    for sku in skus:
        db.add(OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                         klant=customer.name, quantity=quantity, booked_count=0,
                         delivery_day="wednesday"))
        db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                                quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    db.refresh(order)
    return order


def _scan_location(client, token, order_id, code):
    return client.post("/api/picking/scan-location",
                       json={"order_id": order_id, "location_code": code},
                       headers=auth_header(token))


def _scan_ean(client, token, order_id, ean, location_code=None):
    body = {"order_id": order_id, "ean": ean}
    if location_code is not None:
        body["location_code"] = location_code
    return client.post("/api/picking/scan-ean", json=body, headers=auth_header(token))


def test_scan_location_lists_products(client, db, courier_token):
    org = _org(db)
    sku = _sku(db, org, "SOK-1", "8711111111111")
    loc = _location(db, "AB123", sku)
    order = _order(db, org, sku)

    resp = _scan_location(client, courier_token, order.id, "AB123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["location_code"] == "AB123"
    assert [s["sku_id"] for s in data["skus"]] == [sku.id]
    assert loc.id  # linked


def test_scan_location_unknown_code(client, db, courier_token):
    org = _org(db, "pl-unknown")
    sku = _sku(db, org, "SOK-U", "8711111111112")
    order = _order(db, org, sku)

    resp = _scan_location(client, courier_token, order.id, "NOPE")
    assert resp.status_code == 404


def test_scan_location_no_order_products(client, db, courier_token):
    org = _org(db, "pl-empty")
    sku = _sku(db, org, "SOK-E", "8711111111113")
    other = _sku(db, org, "SOK-OTHER", "8711111111114")
    _location(db, "ZZ999", other)  # location has a product, but not on the order
    order = _order(db, org, sku)

    resp = _scan_location(client, courier_token, order.id, "ZZ999")
    assert resp.status_code == 400


def test_scan_ean_requires_location_when_located(client, db, courier_token):
    org = _org(db, "pl-req")
    sku = _sku(db, org, "SOK-R", "8711111111115")
    _location(db, "AB123", sku)
    order = _order(db, org, sku)

    resp = _scan_ean(client, courier_token, order.id, "8711111111115")
    assert resp.status_code == 400
    assert "locatie" in resp.json()["detail"].lower()


def test_scan_ean_rejects_wrong_location(client, db, courier_token):
    org = _org(db, "pl-wrong")
    sku = _sku(db, org, "SOK-W", "8711111111116")
    _location(db, "AB123", sku)
    _location(db, "CD201")  # exists but sku not on it
    order = _order(db, org, sku)

    resp = _scan_ean(client, courier_token, order.id, "8711111111116", "CD201")
    assert resp.status_code == 400
    assert resp.json()["detail"].count("CD201") >= 1


def test_scan_ean_books_from_correct_location(client, db, courier_token):
    org = _org(db, "pl-ok")
    sku = _sku(db, org, "SOK-OK", "8711111111117")
    _location(db, "AB123", sku)
    order = _order(db, org, sku)

    resp = _scan_ean(client, courier_token, order.id, "8711111111117", "AB123")
    assert resp.status_code == 200
    assert resp.json()["booked_quantity"] == 1


def test_scan_location_inactive_rejected(client, db, courier_token):
    org = _org(db, "pl-inactive")
    sku = _sku(db, org, "SOK-IA", "8711111111120")
    loc = _location(db, "AB123", sku)
    loc.active = False
    db.commit()
    order = _order(db, org, sku)

    resp = _scan_location(client, courier_token, order.id, "AB123")
    assert resp.status_code == 404


def test_inactive_location_not_enforced_on_scan_ean(client, db, courier_token):
    # A product whose only location is inactive falls back to ungated (no shelf
    # to scan), so it stays pickable — deactivating just removes the gate.
    org = _org(db, "pl-ia-ean")
    sku = _sku(db, org, "SOK-IE", "8711111111121")
    loc = _location(db, "AB123", sku)
    loc.active = False
    db.commit()
    order = _order(db, org, sku)

    resp = _scan_ean(client, courier_token, order.id, "8711111111121")
    assert resp.status_code == 200


def test_scan_ean_unlocated_product_needs_no_location(client, db, courier_token):
    org = _org(db, "pl-unloc")
    sku = _sku(db, org, "SOK-N", "8711111111118")  # no location linked
    order = _order(db, org, sku)

    resp = _scan_ean(client, courier_token, order.id, "8711111111118")
    assert resp.status_code == 200
    assert resp.json()["booked_quantity"] == 1
