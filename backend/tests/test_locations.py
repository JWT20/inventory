"""Tests for pick-location management (courier-only, barcode products).

POST/GET/PATCH/DELETE /api/locations and the SKU-link endpoints. Gated to
platform admin + courier via require_warehouse; only barcode SKUs may be linked.
"""
from app.auth import create_token, hash_password
from app.models import Organization, SKU, User
from tests.conftest import auth_header


def _org(db, slug="loc-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _barcode_sku(db, org, code="SOK-1", ean="8711111111111"):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _vision_sku(db, org, code="WIJN-1"):
    sku = SKU(sku_code=code, name=f"Wijn {code}", organization_id=org.id,
              category="wine", product_type="vision")
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _owner_token(db, org):
    owner = User(username="loc-owner", email="loc-owner@local",
                 hashed_password=hash_password("OwnerPass1!"), role="owner",
                 organization_id=org.id, is_verified=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return create_token(owner.id)


def _create(client, token, code="AB123", **kw):
    return client.post("/api/locations", json={"code": code, **kw},
                       headers=auth_header(token))


def test_courier_creates_and_lists_location(client, db, courier_token):
    resp = _create(client, courier_token, "AB123", rij="A", kast="B", plank="1")
    assert resp.status_code == 201
    assert resp.json()["code"] == "AB123"

    listing = client.get("/api/locations", headers=auth_header(courier_token))
    assert listing.status_code == 200
    codes = [l["code"] for l in listing.json()]
    assert "AB123" in codes


def test_duplicate_code_rejected(client, db, courier_token):
    assert _create(client, courier_token, "DUP1").status_code == 201
    assert _create(client, courier_token, "DUP1").status_code == 409


def test_link_barcode_sku(client, db, courier_token):
    org = _org(db)
    sku = _barcode_sku(db, org)
    loc_id = _create(client, courier_token, "LNK1").json()["id"]

    resp = client.post(f"/api/locations/{loc_id}/skus",
                       json={"sku_id": sku.id}, headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert [s["sku_id"] for s in resp.json()["skus"]] == [sku.id]


def test_link_vision_sku_rejected(client, db, courier_token):
    org = _org(db, "vis-org")
    sku = _vision_sku(db, org)
    loc_id = _create(client, courier_token, "VIS1").json()["id"]

    resp = client.post(f"/api/locations/{loc_id}/skus",
                       json={"sku_id": sku.id}, headers=auth_header(courier_token))
    assert resp.status_code == 400
    assert "barcode" in resp.json()["detail"].lower()


def test_unlink_sku(client, db, courier_token):
    org = _org(db, "unl-org")
    sku = _barcode_sku(db, org)
    loc_id = _create(client, courier_token, "UNL1").json()["id"]
    client.post(f"/api/locations/{loc_id}/skus", json={"sku_id": sku.id},
                headers=auth_header(courier_token))

    resp = client.delete(f"/api/locations/{loc_id}/skus/{sku.id}",
                         headers=auth_header(courier_token))
    assert resp.status_code == 204

    listing = client.get("/api/locations", headers=auth_header(courier_token))
    loc = next(l for l in listing.json() if l["id"] == loc_id)
    assert loc["skus"] == []


def test_available_skus_only_barcode(client, db, courier_token):
    org = _org(db, "avail-org")
    barcode = _barcode_sku(db, org, "SOK-AV", "8712222222222")
    _vision_sku(db, org, "WIJN-AV")

    resp = client.get("/api/locations/available-skus", headers=auth_header(courier_token))
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert barcode.id in ids
    assert all(s["sku_code"] != "WIJN-AV" for s in resp.json())


def test_owner_forbidden(client, db):
    org = _org(db, "forbid-org")
    token = _owner_token(db, org)
    assert _create(client, token, "OWN1").status_code == 403
