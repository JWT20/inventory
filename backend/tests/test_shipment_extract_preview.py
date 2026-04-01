from unittest.mock import AsyncMock, patch

from tests.conftest import auth_header


class _TmpStorage:
    def __init__(self, base):
        self.base = base

    def save(self, key: str, content: bytes) -> str:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def url(self, key: str) -> str:
        return f"/api/files/{key}"


def test_extract_preview_maps_sku_code(client, db, admin_token, sample_sku, tmp_path):
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-123",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "WINE-001",
                "description": "Test wine line",
                "quantity_boxes": 6,
                "confidence": 0.93,
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05, "page": 1},
            }
        ],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(admin_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["supplier_name"] == "Anfors"
    assert body["document_type"] == "pakbon"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["matched_sku_code"] == "WINE-001"
    assert body["lines"][0]["quantity_boxes"] == 6


def test_extract_preview_requires_warehouse_role(client, owner_token):
    resp = client.post(
        "/api/shipments/extract-preview",
        headers=auth_header(owner_token),
        files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
    )

    assert resp.status_code == 403
