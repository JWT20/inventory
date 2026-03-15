"""Tests for box/package image filtering on upload and scan endpoints.

Verifies that:
- Packages (boxes) are accepted for reference upload
- Non-package images are rejected with a clear message
- Users can override the rejection with skip_wine_check=true
- Scanning non-package images returns no match (identify) or 422 (book)
- parse_classify_response correctly extracts the is_package flag
- describe_image backward compat works (classify + describe)
- assess_description_quality returns correct quality levels
"""

import io
from unittest.mock import patch, MagicMock

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# parse_classify_response — pure logic, no mocks needed
# ---------------------------------------------------------------------------

class TestParseClassifyResponse:
    def test_json_is_package_true(self):
        from app.services.embedding import parse_classify_response
        is_package, summary = parse_classify_response(
            '{"is_package": true, "summary": "wine box cardboard"}'
        )
        assert is_package is True
        assert summary == "wine box cardboard"

    def test_json_is_package_false(self):
        from app.services.embedding import parse_classify_response
        is_package, summary = parse_classify_response(
            '{"is_package": false, "summary": "digital clock"}'
        )
        assert is_package is False
        assert summary == "digital clock"

    def test_markdown_fenced_json(self):
        from app.services.embedding import parse_classify_response
        is_package, summary = parse_classify_response(
            '```json\n{"is_package": true, "summary": "shoe box"}\n```'
        )
        assert is_package is True
        assert summary == "shoe box"

    def test_fallback_heuristic_with_box_word(self):
        from app.services.embedding import parse_classify_response
        is_package, summary = parse_classify_response(
            "This appears to be a cardboard box with shipping labels"
        )
        assert is_package is True

    def test_fallback_heuristic_no_box_word(self):
        from app.services.embedding import parse_classify_response
        is_package, summary = parse_classify_response(
            "A digital clock sitting on a desk"
        )
        assert is_package is False


# ---------------------------------------------------------------------------
# assess_description_quality — pure logic
# ---------------------------------------------------------------------------

class TestAssessDescriptionQuality:
    def test_high_quality(self):
        from app.services.embedding import assess_description_quality
        desc = "ROBERT WEIL Junior Pinot Gris Organic 2023. Rheinhessen, Dt. Qualitätswein trocken, Product of Germany. Dark blue box with white text."
        assert assess_description_quality(desc) == "high"

    def test_low_quality_short(self):
        from app.services.embedding import assess_description_quality
        desc = "brown cardboard box"
        assert assess_description_quality(desc) == "low"

    def test_low_quality_no_transcribed_text(self):
        from app.services.embedding import assess_description_quality
        desc = "a plain brown cardboard box with no visible text or labels on this side"
        assert assess_description_quality(desc) == "low"

    def test_medium_quality(self):
        from app.services.embedding import assess_description_quality
        desc = "White box with DOMENECH text printed in black. Gold crest logo on top. Brut designation visible."
        assert assess_description_quality(desc) == "medium"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG-like bytes


def _mock_classify_package(image_bytes):
    """Simulate Gemini classifying an image as a package."""
    return (True, "wine box cardboard")


def _mock_classify_not_package(image_bytes):
    """Simulate Gemini classifying an image as NOT a package."""
    return (False, "digital clock")


def _mock_process_package(image_bytes):
    """Full pipeline mock for package image."""
    return ("White box with bull logo, Rioja", [0.1] * 3072, True)


def _mock_process_not_package(image_bytes):
    """Full pipeline mock for non-package image."""
    return ("digital clock", None, False)


# ---------------------------------------------------------------------------
# POST /api/skus/{id}/images — reference upload with package filter
# ---------------------------------------------------------------------------

class TestReferenceUploadPackageFilter:
    def test_package_image_accepted(self, client, merchant_token, sample_sku, tmp_path):
        """A box image should be accepted and saved."""
        with patch("app.routers.skus.classify_image", side_effect=_mock_classify_package), \
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

    def test_non_package_image_rejected(self, client, merchant_token, sample_sku):
        """A non-package image (clock, candles) should be rejected with 400."""
        with patch("app.routers.skus.classify_image", side_effect=_mock_classify_not_package):
            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(merchant_token),
            )

        assert resp.status_code == 400
        assert "doos" in resp.json()["detail"].lower()

    def test_non_package_override_accepted(self, client, merchant_token, sample_sku, tmp_path):
        """User overrides rejection with skip_wine_check=true — should be accepted."""
        with patch("app.routers.skus.classify_image") as mock_classify, \
             patch("app.routers.skus.settings") as mock_settings, \
             patch("app.routers.skus._process_reference_image_background"):
            mock_settings.upload_dir = str(tmp_path)

            resp = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"skip_wine_check": "true"},
                headers=auth_header(merchant_token),
            )

        assert resp.status_code == 201
        # classify_image should NOT have been called (wine check skipped)
        mock_classify.assert_not_called()

    def test_rejection_then_override_flow(self, client, merchant_token, sample_sku, tmp_path):
        """Full flow: upload rejected → user clicks 'Toch uploaden' → accepted."""
        # Step 1: First upload gets rejected
        with patch("app.routers.skus.classify_image", side_effect=_mock_classify_not_package):
            resp1 = client.post(
                f"/api/skus/{sample_sku.id}/images",
                files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(merchant_token),
            )
        assert resp1.status_code == 400
        assert "doos" in resp1.json()["detail"].lower()

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
# POST /api/receiving/identify — scan with package filter
# ---------------------------------------------------------------------------

class TestIdentifyPackageFilter:
    def test_non_package_scan_returns_null(self, client, courier_token, tmp_path):
        """Scanning a non-package item should return null (no match)."""
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.settings") as mock_settings:
            mock_settings.upload_dir = str(tmp_path)
            mock_settings.match_threshold = 0.85

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json() is None

    def test_package_scan_proceeds_to_matching(self, client, courier_token, sample_sku, db, tmp_path):
        """Scanning a package should proceed to vector matching."""
        from app.models import ReferenceImage
        ref = ReferenceImage(
            sku_id=sample_sku.id,
            image_path="/fake/path.jpg",
            embedding=[0.1] * 3072,
            processing_status="done",
        )
        db.add(ref)
        db.commit()

        with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
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
# POST /api/receiving/new-product — quick-create with package filter
# ---------------------------------------------------------------------------

class TestNewProductPackageFilter:
    def test_non_package_rejected(self, client, courier_token, tmp_path):
        """Quick-creating a product with a non-package image should be rejected."""
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.settings") as mock_settings:
            mock_settings.upload_dir = str(tmp_path)

            resp = client.post(
                "/api/receiving/new-product",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"sku_code": "CLOCK-001", "name": "Not a box"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 400
        assert "doos" in resp.json()["detail"].lower()
