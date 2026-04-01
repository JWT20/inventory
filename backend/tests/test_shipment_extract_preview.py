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


def test_confirm_from_preview_creates_and_books_shipment(client, db, admin_token, sample_sku):
    resp = client.post(
        "/api/shipments/confirm-from-preview",
        headers=auth_header(admin_token),
        json={
            "supplier_name": "Anfors",
            "reference": "PKB-200",
            "save_mappings": True,
            "auto_book": True,
            "lines": [
                {
                    "supplier_code": "WINE-001",
                    "sku_id": sample_sku.id,
                    "quantity_boxes": 4,
                }
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "booked"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["quantity"] == 4


def test_save_unmatched_queue_items(client, db, admin_token):
    resp = client.post(
        "/api/shipments/unmatched",
        headers=auth_header(admin_token),
        json={
            "supplier_name": "Anfors",
            "reference": "PKB-404",
            "document_type": "pakbon",
            "image_key": "shipment_docs/abc.jpg",
            "lines": [
                {
                    "supplier_code": "AF999",
                    "description": "Unknown wine",
                    "quantity_boxes": 3,
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04, "page": 1},
                }
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "open"
    assert body[0]["supplier_code"] == "AF999"
