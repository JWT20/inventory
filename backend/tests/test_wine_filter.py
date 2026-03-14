"""Tests for wine box image filtering on upload and scan endpoints.

Verifies that:
- Wine boxes are accepted for reference upload
- Non-wine images are rejected with a clear message
- Users can override the rejection with skip_wine_check=true
- Scanning non-wine images returns no match (identify) or 400 (book)
- parse_vision_response correctly extracts the WINE_PRODUCT flag
"""

import io
from unittest.mock import patch, MagicMock

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# parse_vision_response — pure logic, no mocks needed
# ---------------------------------------------------------------------------

class TestParseVisionResponse:
    def test_wine_product_yes(self):
        from app.services.embedding import parse_vision_response
        is_wine, desc = parse_vision_response(
            "WINE_PRODUCT: YES\nChâteau Margaux 2015 Grand Vin Bordeaux"
        )
        assert is_wine is True
        assert desc == "Château Margaux 2015 Grand Vin Bordeaux"

    def test_wine_product_no(self):
        from app.services.embedding import parse_vision_response
        is_wine, desc = parse_vision_response(
            "WINE_PRODUCT: NO\nA black cycling shoe box with Time branding"
        )
        assert is_wine is False
        assert desc == "A black cycling shoe box with Time branding"

    def test_missing_flag_assumes_wine(self):
        from app.services.embedding import parse_vision_response
        is_wine, desc = parse_vision_response(
            "Château Margaux 2015 Grand Vin Bordeaux red wine"
        )
        assert is_wine is True
        assert "Château Margaux" in desc

    def test_case_insensitive_yes(self):
        from app.services.embedding import parse_vision_response
        is_wine, _ = parse_vision_response("wine_product: yes\nSome wine")
        assert is_wine is True

    def test_case_insensitive_no(self):
        from app.services.embedding import parse_vision_response
        is_wine, _ = parse_vision_response("WINE_PRODUCT: No\nA shoe box")
        assert is_wine is False

    def test_multiline_description(self):
        from app.services.embedding import parse_vision_response
        raw = "WINE_PRODUCT: YES\nLine one\nLine two\nLine three"
        is_wine, desc = parse_vision_response(raw)
        assert is_wine is True
        assert desc == "Line one\nLine two\nLine three"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG-like bytes


def _mock_describe_wine(image_bytes):
    """Simulate Gemini classifying an image as wine."""
    return ("White box with bull logo, Rioja Denominación de Origen, 6x750ml", True)


def _mock_describe_not_wine(image_bytes):
    """Simulate Gemini classifying an image as NOT wine."""
    return ("A black cycling shoe box with Time OSMOS branding, size 43 EU", False)


def _mock_process_wine(image_bytes):
    """Full pipeline mock for wine image."""
    return ("White box with bull logo, Rioja", [0.1] * 3072, True)


def _mock_process_not_wine(image_bytes):
    """Full pipeline mock for non-wine image."""
    return ("A black shoe box", None, False)


# ---------------------------------------------------------------------------
# POST /api/skus/{id}/images — reference upload with wine filter
# ---------------------------------------------------------------------------

class TestReferenceUploadWineFilter:
    def test_wine_image_accepted(self, client, merchant_token, sample_sku, tmp_path):
        """A wine box image should be accepted and saved."""
        with patch("app.routers.skus.describe_image", side_effect=_mock_describe_wine), \
             patch("app.routers.skus.settings") as mock_settings, \
             patch("app.routers.skus._process_reference_image_background"):
            mock_settings.upload_dir = str(tmp_path)

            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("wine.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(merchant_token),
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["processing_status"] == "pending"

    def test_non_wine_image_rejected(self, client, merchant_token, sample_sku):
        """A shoe box image should be rejected with 400."""
        with patch("app.routers.skus.describe_image", side_effect=_mock_describe_not_wine):
            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("shoe.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(merchant_token),
            )

        assert resp.status_code == 400
        assert "wijndoos" in resp.json()["detail"].lower()

    def test_non_wine_override_accepted(self, client, merchant_token, sample_sku, tmp_path):
        """User overrides rejection with skip_wine_check=true — should be accepted."""
        with patch("app.routers.skus.describe_image") as mock_desc, \
             patch("app.routers.skus.settings") as mock_settings, \
             patch("app.routers.skus._process_reference_image_background"):
            mock_settings.upload_dir = str(tmp_path)

            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("shoe.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"skip_wine_check": "true"},
                headers=auth_header(merchant_token),
            )

        assert resp.status_code == 201
        # describe_image should NOT have been called (wine check skipped)
        mock_desc.assert_not_called()

    def test_rejection_then_override_flow(self, client, merchant_token, sample_sku, tmp_path):
        """Full flow: upload rejected → user clicks 'Toch uploaden' → accepted."""
        # Step 1: First upload gets rejected
        with patch("app.routers.skus.describe_image", side_effect=_mock_describe_not_wine):
            resp1 = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(merchant_token),
            )
        assert resp1.status_code == 400
        assert "wijndoos" in resp1.json()["detail"].lower()

        # Step 2: Same image re-uploaded with override
        with patch("app.routers.skus.settings") as mock_settings, \
             patch("app.routers.skus._process_reference_image_background"):
            mock_settings.upload_dir = str(tmp_path)

            resp2 = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"skip_wine_check": "true"},
                headers=auth_header(merchant_token),
            )
        assert resp2.status_code == 201


# ---------------------------------------------------------------------------
# POST /api/receiving/identify — scan with wine filter
# ---------------------------------------------------------------------------

class TestIdentifyWineFilter:
    def test_non_wine_scan_returns_null(self, client, courier_token, tmp_path):
        """Scanning a non-wine item should return null (no match)."""
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_wine), \
             patch("app.routers.receiving.settings") as mock_settings:
            mock_settings.upload_dir = str(tmp_path)
            mock_settings.match_threshold = 0.85

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("shoe.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json() is None

    def test_wine_scan_proceeds_to_matching(self, client, courier_token, sample_sku, db, tmp_path):
        """Scanning a wine box should proceed to vector matching."""
        from app.models import ReferenceImage
        ref = ReferenceImage(
            sku_id=sample_sku.id,
            image_path="/fake/path.jpg",
            embedding=[0.1] * 3072,
            processing_status="done",
        )
        db.add(ref)
        db.commit()

        with patch("app.routers.receiving.process_image", side_effect=_mock_process_wine), \
             patch("app.routers.receiving.settings") as mock_settings, \
             patch("app.routers.receiving.find_best_matches") as mock_match:
            mock_settings.upload_dir = str(tmp_path)
            mock_settings.match_threshold = 0.85
            mock_match.return_value = [(sample_sku, 0.95)]

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("wine.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sku_code"] == "WINE-001"
        assert data["confidence"] == 0.95


# ---------------------------------------------------------------------------
# POST /api/receiving/new-product — quick-create with wine filter
# ---------------------------------------------------------------------------

class TestNewProductWineFilter:
    def test_non_wine_rejected(self, client, courier_token, tmp_path):
        """Quick-creating a product with a non-wine image should be rejected."""
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_wine), \
             patch("app.routers.receiving.settings") as mock_settings:
            mock_settings.upload_dir = str(tmp_path)

            resp = client.post(
                "/api/receiving/new-product",
                files={"file": ("shoe.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"sku_code": "SHOE-001", "name": "Not a wine"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 400
        assert "wijndoos" in resp.json()["detail"].lower()
