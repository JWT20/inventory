"""Tests for SKU CRUD endpoints."""

from tests.conftest import auth_header


WINE_DATA = {
    "category": "wine",
    "attributes": {
        "producent": "Château Test",
        "wijnaam": "Grand Vin",
        "wijntype": "Rood",
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
# GET /api/skus/options
# ---------------------------------------------------------------------------

class TestListSKUOptions:
    def test_options_return_slim_projection(self, client, courier_token, sample_sku):
        resp = client.get("/api/skus/options", headers=auth_header(courier_token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert set(data[0].keys()) == {"id", "sku_code", "name"}
        assert data[0]["sku_code"] == "WINE-001"

    def test_options_active_only(self, client, db, courier_token, sample_sku):
        from app.models import SKU
        db.add(SKU(sku_code="WINE-002", name="Inactive Wine", active=False))
        db.commit()

        resp = client.get(
            "/api/skus/options", params={"active_only": True},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        codes = [s["sku_code"] for s in resp.json()]
        assert codes == ["WINE-001"]

    def test_options_unauthenticated(self, client):
        resp = client.get("/api/skus/options")
        assert resp.status_code == 401

    def test_options_filtered_by_organization(
        self, client, db, courier_token, sample_org,
    ):
        from app.models import Organization, SKU
        other_org = Organization(name="Other Wijnhandel", slug="other-wijnhandel")
        db.add(other_org)
        db.flush()
        own = SKU(sku_code="OWN-1", name="Own", organization_id=sample_org.id)
        other = SKU(sku_code="OTH-1", name="Other", organization_id=other_org.id)
        db.add_all([own, other])
        db.commit()

        resp = client.get(
            "/api/skus/options",
            params={"organization_id": sample_org.id},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        codes = [s["sku_code"] for s in resp.json()]
        assert codes == ["OWN-1"]


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
        assert data["sku_code"] == "CHAT-GRAN-ROO-750"
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
            sku_code="CHAT-GRAN-ROO-750", name="Existing",
            category="wine",
        )
        sku.set_attributes({
            "producent": "Château Test", "wijnaam": "Grand Vin",
            "wijntype": "Rood", "volume": "750",
        })
        db.add(sku)
        db.commit()

        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400

    def test_create_sku_defaults_to_box(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 201
        assert resp.json()["is_bottle"] is False

    def test_create_bottle_sku(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json={**WINE_DATA, "is_bottle": True},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 201
        assert resp.json()["is_bottle"] is True


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

    def test_update_is_bottle(self, client, merchant_token, sample_sku):
        resp = client.patch(
            f"/api/skus/{sample_sku.id}",
            json={"is_bottle": True},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_bottle"] is True
        assert data["active"] is True  # unchanged

        resp = client.patch(
            f"/api/skus/{sample_sku.id}",
            json={"is_bottle": False},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        assert resp.json()["is_bottle"] is False

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

    def test_owner_deletes_own_org_sku(self, client, db, merchant_token, merchant_user, sample_sku):
        sample_sku.organization_id = merchant_user.organization_id
        db.commit()

        resp = client.delete(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 204

    def test_owner_cannot_delete_other_org_sku(self, client, merchant_token, sample_sku):
        # sample_sku has no organization_id, merchant belongs to an org → 404
        resp = client.delete(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 404

    def test_delete_nonexistent_sku(self, client, admin_token):
        resp = client.delete(
            "/api/skus/9999",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Courier-specific access: org filter on list + photo CRUD
# ---------------------------------------------------------------------------

class TestCourierAccess:
    def test_courier_filters_skus_by_organization(
        self, client, db, courier_token, sample_org,
    ):
        from app.models import Organization, SKU
        other_org = Organization(name="Other Wijnhandel", slug="other-wijnhandel")
        db.add(other_org)
        db.flush()
        own = SKU(sku_code="OWN-1", name="Own", organization_id=sample_org.id)
        other = SKU(sku_code="OTH-1", name="Other", organization_id=other_org.id)
        db.add_all([own, other])
        db.commit()

        resp = client.get(
            "/api/skus",
            params={"organization_id": sample_org.id},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        codes = [s["sku_code"] for s in resp.json()]
        assert codes == ["OWN-1"]

    def test_courier_can_upload_reference_image(
        self, client, courier_token, sample_sku, db, tmp_path,
    ):
        import io
        from unittest.mock import patch
        from app.services.storage import LocalStorage
        from tests.test_wine_filter import (
            FAKE_IMAGE,
            _mock_classify_and_describe_package,
            _mock_generate_embedding,
            _mock_no_duplicate,
        )

        with patch("app.routers.skus.classify_and_describe", side_effect=_mock_classify_and_describe_package), \
             patch("app.routers.skus.generate_embedding", side_effect=_mock_generate_embedding), \
             patch("app.routers.skus._check_duplicate_embedding", side_effect=_mock_no_duplicate), \
             patch("app.routers.skus.storage", LocalStorage(str(tmp_path))):
            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("wine.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(courier_token),
            )
        assert resp.status_code == 201

    def test_courier_can_delete_reference_image(
        self, client, courier_token, sample_sku, db, tmp_path,
    ):
        from unittest.mock import patch
        from app.models import ReferenceImage
        from app.services.storage import LocalStorage

        storage = LocalStorage(str(tmp_path))
        storage.save("reference_images/x.jpg", b"\xff\xd8\xff\xe0")
        img = ReferenceImage(
            sku_id=sample_sku.id,
            image_path="reference_images/x.jpg",
            processing_status="done",
        )
        db.add(img)
        db.commit()

        with patch("app.routers.skus.storage", storage):
            resp = client.delete(
                f"/api/skus/{sample_sku.id}/images/{img.id}",
                headers=auth_header(courier_token),
            )
        assert resp.status_code == 204
        assert db.get(ReferenceImage, img.id) is None

    def test_courier_cannot_delete_sku(self, client, courier_token, sample_sku):
        resp = client.delete(
            f"/api/skus/{sample_sku.id}",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_courier_can_read_other_org_sku_and_images(
        self, client, courier_token, db, sample_org,
    ):
        from app.models import ReferenceImage, SKU
        sku = SKU(sku_code="ORG-1", name="Other Org Wine", organization_id=sample_org.id)
        db.add(sku)
        db.flush()
        db.add(ReferenceImage(
            sku_id=sku.id,
            image_path="reference_images/x.jpg",
            processing_status="done",
        ))
        db.commit()

        resp = client.get(
            f"/api/skus/{sku.id}",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200

        resp = client.get(
            f"/api/skus/{sku.id}/images",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = client.get(
            f"/api/skus/{sku.id}/images/status",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
