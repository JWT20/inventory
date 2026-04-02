from unittest.mock import AsyncMock, patch

from app.models import SKU, SupplierSKUMapping
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


def test_extract_preview_does_not_fallback_to_direct_sku_code(client, db, admin_token, sample_sku, tmp_path):
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
    assert body["lines"][0]["matched_sku_code"] is None
    assert body["lines"][0]["quantity_boxes"] == 6


def test_extract_preview_requires_warehouse_role(client, owner_token):
    resp = client.post(
        "/api/shipments/extract-preview",
        headers=auth_header(owner_token),
        files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
    )

    assert resp.status_code == 403


def test_extract_preview_maps_using_supplier_mapping(
    client, db, admin_token, sample_sku, tmp_path
):
    mapped_sku = SKU(
        sku_code="MAPPED-001",
        name="Mapped SKU",
        organization_id=None,
    )
    db.add(mapped_sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=None,
        supplier_name="ANFORS",
        supplier_code="WINE-001",
        sku_id=mapped_sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-123",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "WINE-001",
                "description": "Mapped first",
                "quantity_boxes": 2,
                "confidence": 0.88,
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
            data={"document_type": "pakbon", "supplier_name": "Anfors"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"][0]["matched_sku_code"] == "MAPPED-001"


def test_extract_preview_uses_case_insensitive_supplier_mapping(
    client, db, admin_token, sample_sku, tmp_path
):
    db.add(SupplierSKUMapping(
        organization_id=None,
        supplier_name="ANFORS",
        supplier_code="WINE-ABC",
        sku_id=sample_sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "anfors",
        "reference": "PKB-999",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "wine-abc",
                "description": "lowercase should still match",
                "quantity_boxes": 1,
                "confidence": 0.9,
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
    assert body["lines"][0]["matched_sku_code"] == sample_sku.sku_code


def test_extract_preview_llm_matches_when_supplier_code_missing(
    client, db, admin_token, sample_sku, tmp_path
):
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-777",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "",
                "description": "Sample product name",
                "quantity_boxes": 3,
                "confidence": 0.2,
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05, "page": 1},
            }
        ],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.match_shipment_article_name", new=AsyncMock(return_value=(sample_sku.sku_code, 0.86))), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(admin_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"][0]["matched_sku_code"] is None
    assert body["lines"][0]["needs_confirmation"] is True
    assert body["lines"][0]["match_source"] == "llm_suggestion"
    assert body["lines"][0]["candidate_matches"][0]["sku_code"] == sample_sku.sku_code
    assert body["lines"][0]["confidence"] == 0.86


def test_extract_preview_llm_low_confidence_does_not_autolink(
    client, db, admin_token, sample_sku, tmp_path
):
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-778",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "",
                "description": "Another product name",
                "quantity_boxes": 3,
                "confidence": 0.2,
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05, "page": 1},
            }
        ],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.match_shipment_article_name", new=AsyncMock(return_value=(sample_sku.sku_code, 0.41))), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(admin_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"][0]["matched_sku_code"] is None
    assert body["lines"][0]["needs_confirmation"] is True


def test_extract_preview_uses_llm_quantity_boxes_without_backend_normalization(
    client, db, admin_token, tmp_path
):
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-779",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "AFO161023",
                "description": "PMC Burgenland Chardonnay23 3 ct6 18 fl",
                "quantity_boxes": 18,
                "evidence": {
                    "line_text": "PMC Burgenland Chardonnay23 3 ct6 18 fl",
                    "quantity_text": "18 fl",
                    "packaging_text": "ct6",
                },
                "confidence": 0.94,
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
    assert body["lines"][0]["quantity_boxes"] == 18


def test_supplier_mapping_crud_and_confirm_flow(client, db, owner_token, owner_user):
    sku = SKU(sku_code="SKU-MAP-1", name="Map 1", organization_id=owner_user.organization_id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    confirm = client.post(
        "/api/shipments/confirm-line-match",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Anfors",
            "supplier_code": "abc-123",
            "chosen_sku_id": sku.id,
            "persist_mapping": True,
        },
    )
    assert confirm.status_code == 200
    mapping_id = confirm.json()["id"]
    assert confirm.json()["supplier_name"] == "ANFORS"
    assert confirm.json()["supplier_code"] == "ABC-123"

    listed = client.get(
        "/api/supplier-mappings",
        headers=auth_header(owner_token),
        params={"supplier_name": "anfors"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == mapping_id

    deleted = client.delete(
        f"/api/supplier-mappings/{mapping_id}",
        headers=auth_header(owner_token),
    )
    assert deleted.status_code == 204
