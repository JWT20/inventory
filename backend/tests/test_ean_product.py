"""Tests for EAN + product_type on SKUs (Fase 1 barcode-product fundament)."""

from app.schemas import is_valid_ean13
from tests.conftest import auth_header


# A barcode sock product: non-wine category, explicit name + sku_code + EAN.
def _barcode_payload(**overrides):
    payload = {
        "category": "overig",
        "name": "Wielersok zwart M",
        "sku_code": "SOK-ZW-M",
        "product_type": "barcode",
        "ean": "8712345678906",  # valid EAN-13
    }
    payload.update(overrides)
    return payload


WINE_DATA = {
    "category": "wine",
    "attributes": {
        "producent": "Château Test",
        "wijnaam": "Grand Vin",
        "wijntype": "Rood",
        "volume": "750",
    },
}


class TestEan13Checkdigit:
    def test_valid_ean13(self):
        assert is_valid_ean13("8712345678906")
        assert is_valid_ean13("4006381333931")

    def test_wrong_checkdigit(self):
        assert not is_valid_ean13("8712345678901")

    def test_wrong_length_or_non_digits(self):
        assert not is_valid_ean13("87123456789")  # 11 digits
        assert not is_valid_ean13("ABC2345678906")
        assert not is_valid_ean13("")


class TestCreateBarcodeProduct:
    def test_create_barcode_product(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=_barcode_payload(),
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["product_type"] == "barcode"
        assert body["ean"] == "8712345678906"

    def test_barcode_requires_ean(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=_barcode_payload(ean=None),
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 422

    def test_barcode_rejects_invalid_ean(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=_barcode_payload(ean="8712345678901"),
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 422

    def test_duplicate_ean_in_org_rejected(self, client, merchant_token):
        first = client.post(
            "/api/skus", json=_barcode_payload(),
            headers=auth_header(merchant_token),
        )
        assert first.status_code == 201
        dup = client.post(
            "/api/skus",
            json=_barcode_payload(sku_code="SOK-ZW-L", name="Wielersok zwart L"),
            headers=auth_header(merchant_token),
        )
        assert dup.status_code == 400
        assert "EAN" in dup.json()["detail"]


class TestWineStaysVision:
    def test_wine_defaults_to_vision_without_ean(self, client, merchant_token):
        resp = client.post(
            "/api/skus", json=WINE_DATA,
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["product_type"] == "vision"
        assert body["ean"] is None

    def test_vision_rejects_ean(self, client, merchant_token):
        resp = client.post(
            "/api/skus",
            json={**WINE_DATA, "product_type": "vision", "ean": "8712345678906"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 422
