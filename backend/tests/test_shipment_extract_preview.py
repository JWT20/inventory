from unittest.mock import AsyncMock, patch

from app.models import Organization, SKU, SupplierSKUMapping
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


def test_extract_preview_forbidden_for_customer(client, customer_token):
    resp = client.post(
        "/api/shipments/extract-preview",
        headers=auth_header(customer_token),
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


def test_extract_preview_falls_back_to_unique_supplier_code(
    client, db, owner_token, owner_user, tmp_path
):
    sku = SKU(
        sku_code="RIOJA-FLES",
        name="Rioja Reserva",
        organization_id=owner_user.organization_id,
        is_bottle=True,
    )
    db.add(sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="AFS290021",
        sku_id=sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors-Imperial",
        "reference": "PKB-ALIAS",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [{
            "supplier_code": "AFS290021",
            "description": "Rioja Reserva Vina Alberdi",
            "quantity": 8,
            "quantity_unit": "pieces",
            "confidence": 0.95,
        }],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    line = resp.json()["lines"][0]
    assert line["matched_sku_code"] == "RIOJA-FLES"
    assert line["quantity_boxes"] == 8
    assert line["match_source"] == "supplier_mapping"


def test_extract_preview_does_not_guess_ambiguous_supplier_code(
    client, db, owner_token, owner_user, tmp_path
):
    first_sku = SKU(
        sku_code="CODE-FIRST",
        name="First wine",
        organization_id=owner_user.organization_id,
    )
    second_sku = SKU(
        sku_code="CODE-SECOND",
        name="Second wine",
        organization_id=owner_user.organization_id,
    )
    db.add_all([first_sku, second_sku])
    db.flush()
    db.add_all([
        SupplierSKUMapping(
            organization_id=owner_user.organization_id,
            supplier_name="ANFORS",
            supplier_code="SHARED-42",
            sku_id=first_sku.id,
        ),
        SupplierSKUMapping(
            organization_id=owner_user.organization_id,
            supplier_name="OTHER-SUPPLIER",
            supplier_code="SHARED-42",
            sku_id=second_sku.id,
        ),
    ])
    db.commit()

    mocked = {
        "supplier_name": "Anfors-Imperial",
        "reference": "PKB-AMBIGUOUS",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [{
            "supplier_code": "SHARED-42",
            "description": "Ambiguous wine",
            "quantity_boxes": 1,
            "confidence": 0.9,
        }],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    line = resp.json()["lines"][0]
    assert line["matched_sku_id"] is None
    assert line["match_source"] == "unresolved"


def test_unique_supplier_code_fallback_is_scoped_to_organization(
    client, db, owner_token, owner_user, tmp_path
):
    other_org = Organization(name="Other fallback org", slug="other-fallback-org")
    db.add(other_org)
    db.flush()
    own_sku = SKU(
        sku_code="OWN-FALLBACK",
        name="Own fallback wine",
        organization_id=owner_user.organization_id,
    )
    other_sku = SKU(
        sku_code="OTHER-FALLBACK",
        name="Other fallback wine",
        organization_id=other_org.id,
    )
    db.add_all([own_sku, other_sku])
    db.flush()
    db.add_all([
        SupplierSKUMapping(
            organization_id=owner_user.organization_id,
            supplier_name="ANFORS",
            supplier_code="ORG-CODE",
            sku_id=own_sku.id,
        ),
        SupplierSKUMapping(
            organization_id=other_org.id,
            supplier_name="OTHER-SUPPLIER",
            supplier_code="ORG-CODE",
            sku_id=other_sku.id,
        ),
    ])
    db.commit()

    mocked = {
        "supplier_name": "Anfors-Imperial",
        "reference": "PKB-ORG-FALLBACK",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [{
            "supplier_code": "ORG-CODE",
            "description": "Own organization wine",
            "quantity_boxes": 1,
            "confidence": 0.9,
        }],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    assert resp.json()["lines"][0]["matched_sku_code"] == "OWN-FALLBACK"


def test_extract_preview_bottle_mapping_counts_pieces_one_to_one(
    client, db, admin_token, tmp_path
):
    """A line mapped to a bottle SKU keeps the bottle count (no division by 6)."""
    bottle_sku = SKU(
        sku_code="FLES-CAVA",
        name="Cava 0,0",
        organization_id=None,
        is_bottle=True,
    )
    db.add(bottle_sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=None,
        supplier_name="ANFORS",
        supplier_code="CAVA-12",
        sku_id=bottle_sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-FLES",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "CAVA-12",
                "description": "Cava alcoholvrij",
                "quantity": 12,
                "quantity_unit": "pieces",
                "confidence": 0.9,
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
    line = resp.json()["lines"][0]
    assert line["matched_sku_code"] == "FLES-CAVA"
    assert line["is_bottle"] is True
    assert line["quantity_boxes"] == 12  # flessen 1-op-1, niet 12 // 6 == 2
    assert line["quantity_unit"] == "pieces"
    assert line["needs_confirmation"] is False


def test_extract_preview_bottle_mapping_box_unit_needs_confirmation(
    client, db, admin_token, tmp_path
):
    """Boxes/colli on a bottle SKU is ambiguous: 0 booked, operator confirms."""
    bottle_sku = SKU(
        sku_code="FLES-CAVA2",
        name="Cava 0,0",
        organization_id=None,
        is_bottle=True,
    )
    db.add(bottle_sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=None,
        supplier_name="ANFORS",
        supplier_code="CAVA-DS",
        sku_id=bottle_sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-FLES2",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "CAVA-DS",
                "description": "Cava alcoholvrij",
                "quantity": 2,
                "quantity_unit": "boxes",
                "confidence": 0.9,
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
    line = resp.json()["lines"][0]
    assert line["is_bottle"] is True
    assert line["quantity_boxes"] == 0
    assert line["quantity_unit"] == "unknown"
    assert line["needs_confirmation"] is True


def test_extract_preview_box_lines_report_is_bottle_false(
    client, db, admin_token, sample_sku, tmp_path
):
    db.add(SupplierSKUMapping(
        organization_id=None,
        supplier_name="ANFORS",
        supplier_code="WINE-BOX",
        sku_id=sample_sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-BOX",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "WINE-BOX",
                "description": "Gewone wijn",
                "quantity": 12,
                "quantity_unit": "pieces",
                "confidence": 0.9,
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
    line = resp.json()["lines"][0]
    assert line["is_bottle"] is False
    assert line["quantity_boxes"] == 2  # doos-SKU houdt de deling door 6


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


def test_extract_preview_uses_own_organization_mapping(
    client, db, owner_token, owner_user, tmp_path
):
    """Each merchant only sees their own org's supplier mappings (no cross-org)."""
    other_org = Organization(name="Andere handelaar", slug="andere-handelaar")
    db.add(other_org)
    db.flush()
    own_sku = SKU(
        sku_code="ORG-A-WINE",
        name="Org A Wine",
        organization_id=owner_user.organization_id,
    )
    other_sku = SKU(
        sku_code="ORG-B-WINE",
        name="Org B Wine",
        organization_id=other_org.id,
    )
    db.add_all([own_sku, other_sku])
    db.flush()
    db.add_all([
        SupplierSKUMapping(
            organization_id=owner_user.organization_id,
            supplier_name="ANFORS",
            supplier_code="WINE-ORG",
            sku_id=own_sku.id,
        ),
        SupplierSKUMapping(
            organization_id=other_org.id,
            supplier_name="ANFORS",
            supplier_code="WINE-ORG",
            sku_id=other_sku.id,
        ),
    ])
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-ORG",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "WINE-ORG",
                "description": "Own org mapping",
                "quantity_boxes": 1,
                "confidence": 0.9,
            }
        ],
    }

    with patch("app.routers.inventory.extract_shipment_document", new=AsyncMock(return_value=mocked)), \
         patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        resp = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("pakbon.jpg", b"fake-image", "image/jpeg")},
            data={"document_type": "pakbon"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"][0]["matched_sku_code"] == "ORG-A-WINE"


def test_extract_preview_requires_manual_link_when_supplier_code_missing(
    client, admin_token, tmp_path
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
    assert body["lines"][0]["matched_sku_id"] is None
    assert body["lines"][0]["matched_sku_code"] is None
    assert body["lines"][0]["matched_sku_name"] is None
    assert body["lines"][0]["needs_confirmation"] is True
    assert body["lines"][0]["match_source"] == "unresolved"
    assert body["lines"][0]["candidate_matches"] == []
    assert body["lines"][0]["confidence"] == 0.2


def test_extract_preview_pieces_are_floored_to_whole_boxes(
    client, db, admin_token, tmp_path
):
    """102 bottles with pack-size 6 should become 17 boxes, regardless of LLM hints."""
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-781",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "AFI810125",
                "description": "Trent, VdD Pinot Grigio25",
                "quantity": 102,
                "quantity_unit": "pieces",
                "evidence": {
                    "line_text": "AFI810125 - Trent, VdD Pinot Grigio25 1 102 132,60 76,50",
                    "quantity_text": "102",
                    "unit_hint": "Flessen",
                },
                "confidence": 0.9,
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
    assert body["lines"][0]["quantity_boxes"] == 17
    assert body["lines"][0]["quantity"] == 102
    assert body["lines"][0]["quantity_unit"] == "pieces"


def test_extract_preview_partial_box_is_ignored(
    client, db, admin_token, tmp_path
):
    """Fewer than 6 bottles means < 1 box — quantity_boxes must be 0."""
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-782",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "AFO161025",
                "description": "Odd partial case",
                "quantity": 5,
                "quantity_unit": "pieces",
                "confidence": 0.9,
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
    assert body["lines"][0]["quantity_boxes"] == 0
    assert body["lines"][0]["quantity"] == 5


def test_extract_preview_boxes_unit_passes_through(
    client, db, admin_token, tmp_path
):
    """When the LLM labels the quantity as boxes, use it directly."""
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-783",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "AFO161025",
                "description": "Direct boxes",
                "quantity": 3,
                "quantity_unit": "boxes",
                "confidence": 0.95,
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
    assert body["lines"][0]["quantity_boxes"] == 3
    assert body["lines"][0]["quantity_unit"] == "boxes"


def test_extract_preview_unknown_unit_flags_for_confirmation(
    client, db, admin_token, tmp_path
):
    """Unknown unit → quantity_boxes stays 0 and the line needs operator review."""
    mocked = {
        "supplier_name": "Anfors",
        "reference": "PKB-784",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "AFO161025",
                "description": "Ambiguous",
                "quantity": 12,
                "quantity_unit": "unknown",
                "confidence": 0.4,
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
    assert body["lines"][0]["quantity_boxes"] == 0
    assert body["lines"][0]["quantity"] == 12
    assert body["lines"][0]["quantity_unit"] == "unknown"
    assert body["lines"][0]["needs_confirmation"] is True


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


def test_extract_preview_text_matches_supplier_mapping(
    client, db, owner_token, owner_user
):
    """Pasted order text runs through the same SKU matching + box conversion."""
    sku = SKU(sku_code="TXT-WINE", name="Tekst Wijn", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="0009532",
        sku_id=sku.id,
    ))
    db.commit()

    mocked = {
        "supplier_name": "Anfors",
        "reference": "",
        "document_type": "unknown",
        "raw_text": "pasted",
        "lines": [
            {
                "supplier_code": "0009532",
                "description": "Vinho Verde Alvarinho 2024 Blanc",
                "quantity": 6,
                "quantity_unit": "pieces",
                "confidence": 0.95,
            }
        ],
    }

    with patch("app.routers.inventory.extract_shipment_text", new=AsyncMock(return_value=mocked)):
        resp = client.post(
            "/api/shipments/extract-preview-text",
            headers=auth_header(owner_token),
            json={"text": "0009532 Vinho Verde Alvarinho 2024 Blanc 6 7,31 43,86", "supplier_name": "Anfors"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image_url"] == ""
    assert body["document_sha256"]
    assert body["lines"][0]["matched_sku_code"] == "TXT-WINE"
    assert body["lines"][0]["quantity_boxes"] == 1
    assert body["lines"][0]["quantity"] == 6


def test_extract_preview_text_unavailable_prompt_returns_503(client, owner_token):
    from app.services.langfuse_client import PromptUnavailableError

    with patch(
        "app.routers.inventory.extract_shipment_text",
        new=AsyncMock(side_effect=PromptUnavailableError("missing")),
    ):
        resp = client.post(
            "/api/shipments/extract-preview-text",
            headers=auth_header(owner_token),
            json={"text": "0009532 Wine 6"},
        )

    assert resp.status_code == 503
    assert "extract-shipment-text" in resp.json()["detail"]


def test_extract_preview_text_forbidden_for_customer(client, customer_token):
    resp = client.post(
        "/api/shipments/extract-preview-text",
        headers=auth_header(customer_token),
        json={"text": "0009532 Wine 6"},
    )
    assert resp.status_code == 403
