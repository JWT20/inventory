"""Tests for SKU CRUD endpoints."""

from tests.conftest import auth_header


WINE_DATA = {
    "category": "wine",
    "attributes": {
        "producent": "Château Test",
        "wijnaam": "Grand Vin",
        "wijntype": "Rood",
        "jaargang": "2020",
        "volume": "750",
    },
}


# ---------------------------------------------------------------------------
# GET /api/skus
# ---------------------------------------------------------------------------

class TestListSKUs:
    def test_list_skus_authenticated(self, client, courier_token, sample_sku):
        resp = client.get("/api/skus", headers=auth_header(courier_token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sku_code"] == "WINE-001"

    def test_list_skus_unauthenticated(self, client):
        resp = client.get("/api/skus")
        assert resp.status_code == 401

    def test_list_skus_active_only(self, client, db, courier_token, sample_sku):
        from app.models import SKU
        inactive = SKU(sku_code="WINE-002", name="Inactive Wine", active=False)
        db.add(inactive)
        db.commit()

        resp = client.get(
            "/api/skus", params={"active_only": True},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        codes = [s["sku_code"] for s in resp.json()]
        assert "WINE-001" in codes
        assert "WINE-002" not in codes

    def test_list_skus_includes_inactive_by_default(self, client, db, courier_token, sample_sku):
        from app.models import SKU
        inactive = SKU(sku_code="WINE-002", name="Inactive Wine", active=False)
        db.add(inactive)
        db.commit()

        resp = client.get("/api/skus", headers=auth_header(courier_token))
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# POST /api/skus
# ---------------------------------------------------------------------------

class TestCreateSKU:
    def test_merchant_creates_sku(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sku_code"] == "CHAT-GRAN-ROO-2020-750"
        assert data["attributes"]["producent"] == "Château Test"
        assert data["attributes"]["wijnaam"] == "Grand Vin"
        assert data["active"] is True
        assert data["image_count"] == 0

    def test_admin_creates_sku(self, client, admin_token):
        resp = client.post(
            "/api/skus",
            json={"category": "wine", "attributes": {**WINE_DATA["attributes"], "producent": "Admin Winery"}},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 201

    def test_courier_cannot_create_sku(self, client, courier_token):
        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_duplicate_sku_code_rejected(self, client, db, merchant_token):
        # Create first
        from app.models import SKU
        sku = SKU(
            sku_code="CHAT-GRAN-ROO-2020-750", name="Existing",
            category="wine",
        )
        sku.set_attributes({
            "producent": "Château Test", "wijnaam": "Grand Vin",
            "wijntype": "Rood", "jaargang": "2020", "volume": "750",
        })
        db.add(sku)
        db.commit()

        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/skus/{id}
# ---------------------------------------------------------------------------

class TestGetSKU:
    def test_get_sku(self, client, courier_token, sample_sku):
        resp = client.get(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert resp.json()["sku_code"] == "WINE-001"

    def test_get_sku_not_found(self, client, courier_token):
        resp = client.get("/api/skus/9999", headers=auth_header(courier_token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/skus/{id}
# ---------------------------------------------------------------------------

class TestUpdateSKU:
    def test_merchant_updates_sku(self, client, merchant_token, sample_sku):
        resp = client.patch(
            f"/api/skus/{sample_sku.id}",
            json={"attributes": {"producent": "Nieuw Domein"}},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        assert resp.json()["attributes"]["producent"] == "Nieuw Domein"

    def test_partial_update(self, client, merchant_token, sample_sku):
        resp = client.patch(
            f"/api/skus/{sample_sku.id}",
            json={"active": False},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        assert data["name"] == "Test Wine"  # unchanged

    def test_courier_cannot_update_sku(self, client, courier_token, sample_sku):
        resp = client.patch(
            f"/api/skus/{sample_sku.id}",
            json={"attributes": {"producent": "Nope"}},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_update_nonexistent_sku(self, client, merchant_token):
        resp = client.patch(
            "/api/skus/9999",
            json={"attributes": {"producent": "Ghost"}},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/skus/{id}
# ---------------------------------------------------------------------------

class TestDeleteSKU:
    def test_admin_deletes_sku(self, client, admin_token, sample_sku):
        resp = client.delete(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204

        # Confirm it's gone
        resp = client.get(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 404

    def test_merchant_cannot_delete_sku(self, client, merchant_token, sample_sku):
        resp = client.delete(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 403

    def test_delete_nonexistent_sku(self, client, admin_token):
        resp = client.delete(
            "/api/skus/9999",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 404
