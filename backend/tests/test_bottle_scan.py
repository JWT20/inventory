"""Tests for the bottle scan mode: match-pool filtering, gate skip and
bottle-aware inbound quantity resolution."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.storage import LocalStorage
from tests.conftest import auth_header
from tests.test_wine_filter import (
    FAKE_EMBEDDING,
    FAKE_IMAGE,
    _mock_process_not_package,
    _mock_process_package,
)


def _tmp_storage(tmp_path):
    return LocalStorage(str(tmp_path))


# ---------------------------------------------------------------------------
# matching.find_best_matches — is_bottle pool filter
# ---------------------------------------------------------------------------

class TestMatchPoolFilter:
    def _run(self, is_bottle):
        from app.services.matching import find_best_matches

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        find_best_matches(mock_db, [0.1] * 3072, is_bottle=is_bottle)
        sql, params = mock_db.execute.call_args.args
        return str(sql), params

    def test_bottle_filter_applied(self):
        sql, params = self._run(True)
        assert "s.is_bottle = :is_bottle" in sql
        assert params["is_bottle"] is True

    def test_box_filter_applied(self):
        sql, params = self._run(False)
        assert "s.is_bottle = :is_bottle" in sql
        assert params["is_bottle"] is False

    def test_no_filter_by_default(self):
        sql, params = self._run(None)
        assert "is_bottle" not in sql
        assert "is_bottle" not in params


# ---------------------------------------------------------------------------
# POST /api/receiving/identify — scan_mode
# ---------------------------------------------------------------------------

class TestIdentifyScanMode:
    def test_bottle_mode_skips_package_gate(
        self, client, courier_token, db, tmp_path
    ):
        """A bottle scan the classifier rejects still proceeds to matching."""
        from app.models import SKU

        bottle_sku = SKU(sku_code="FLES-001", name="Cava 0,0", is_bottle=True)
        db.add(bottle_sku)
        db.commit()

        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.generate_embedding", new_callable=AsyncMock, return_value=FAKE_EMBEDDING), \
             patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
             patch("app.routers.receiving.settings") as mock_settings, \
             patch("app.routers.receiving.find_best_matches") as mock_match:
            mock_settings.match_threshold = 0.85
            mock_settings.ambiguity_margin = 0.03
            mock_match.return_value = [
                (bottle_sku, 0.95, "reference_images/1/fles.jpg", "Cava 0,0 bottle")
            ]

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("fles.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"scan_mode": "bottle"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json()["sku_code"] == "FLES-001"
        assert mock_match.call_args.kwargs["is_bottle"] is True

    def test_box_mode_still_rejects_non_package(self, client, courier_token, tmp_path):
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
             patch("app.routers.receiving.settings") as mock_settings:
            mock_settings.match_threshold = 0.85

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"scan_mode": "box"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json() is None

    def test_box_mode_matches_box_pool_only(
        self, client, courier_token, sample_sku, tmp_path
    ):
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
             patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
             patch("app.routers.receiving.settings") as mock_settings, \
             patch("app.routers.receiving.find_best_matches") as mock_match:
            mock_settings.match_threshold = 0.85
            mock_settings.ambiguity_margin = 0.03
            mock_match.return_value = [
                (sample_sku, 0.95, "reference_images/1/img.jpg", "Rioja box")
            ]

            resp = client.post(
                "/api/receiving/identify",
                files={"file": ("wine.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json()["sku_code"] == "WINE-001"
        assert mock_match.call_args.kwargs["is_bottle"] is False


# ---------------------------------------------------------------------------
# POST /api/receiving/new-product — bottle quick-create
# ---------------------------------------------------------------------------

class TestNewProductBottle:
    def test_bottle_flag_skips_gate_and_sets_unit(
        self, client, courier_token, db, tmp_path
    ):
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.generate_embedding", new_callable=AsyncMock, return_value=FAKE_EMBEDDING), \
             patch("app.routers.receiving._check_duplicate_embedding", return_value=(None, 0.0)), \
             patch("app.routers.receiving.storage", _tmp_storage(tmp_path)):
            resp = client.post(
                "/api/receiving/new-product",
                files={"file": ("fles.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"sku_code": "FLES-NEW", "name": "Nieuwe fles", "is_bottle": "true"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 200
        assert resp.json()["is_bottle"] is True

    def test_box_quick_create_still_rejects_non_package(
        self, client, courier_token, tmp_path
    ):
        with patch("app.routers.receiving.process_image", side_effect=_mock_process_not_package), \
             patch("app.routers.receiving.storage", _tmp_storage(tmp_path)):
            resp = client.post(
                "/api/receiving/new-product",
                files={"file": ("clock.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
                data={"sku_code": "CLOCK-002", "name": "Not a box"},
                headers=auth_header(courier_token),
            )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# inventory._resolve_inbound_quantity — bottle-aware conversion
# ---------------------------------------------------------------------------

class TestResolveInboundQuantityForBottles:
    def test_bottle_pieces_count_one_to_one(self):
        from app.routers.inventory import _resolve_inbound_quantity

        qty, raw, unit = _resolve_inbound_quantity(
            {"quantity": 8, "quantity_unit": "pieces"}, is_bottle=True
        )
        assert (qty, raw, unit) == (8, 8, "pieces")

    def test_bottle_boxes_is_ambiguous(self):
        from app.routers.inventory import _resolve_inbound_quantity

        qty, raw, unit = _resolve_inbound_quantity(
            {"quantity": 3, "quantity_unit": "boxes"}, is_bottle=True
        )
        assert qty == 0
        assert raw == 3
        assert unit == "unknown"

    def test_bottle_missing_unit_is_unknown(self):
        from app.routers.inventory import _resolve_inbound_quantity

        qty, raw, unit = _resolve_inbound_quantity(
            {"quantity": 5}, is_bottle=True
        )
        assert (qty, unit) == (0, "unknown")

    def test_box_sku_keeps_division_by_six(self):
        from app.routers.inventory import _resolve_inbound_quantity

        qty, raw, unit = _resolve_inbound_quantity(
            {"quantity": 12, "quantity_unit": "pieces"}
        )
        assert (qty, raw, unit) == (2, 12, "pieces")
