"""One supplier code carrying several products: propose one, offer the rest.

A wine is often stocked twice on purpose — as the case that goes to the
warehouse and as the loose bottle that goes to the shop shelf — while the
supplier ships both under a single article number.
"""
from unittest.mock import AsyncMock, patch

from app.models import SKU, SupplierSKUMapping
from tests.conftest import auth_header


def _extracted(code: str = "WINE-777", quantity: int = 6) -> dict:
    return {
        "supplier_name": "Anfors",
        "reference": "PKB-777",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": code,
                "description": "Barbebelle Rosé",
                "quantity": quantity,
                "quantity_unit": "pieces",
                "confidence": 0.9,
            }
        ],
    }


def _preview(client, token, extracted: dict):
    with patch(
        "app.routers.inventory.extract_shipment_text", new=AsyncMock(return_value=extracted)
    ):
        return client.post(
            "/api/shipments/extract-preview-text",
            headers=auth_header(token),
            json={"text": "regel", "supplier_name": "Anfors", "document_type": "pakbon"},
        )


def _link(db, org_id, sku: SKU, *, supplier: str = "ANFORS", code: str = "WINE-777"):
    db.add(SupplierSKUMapping(
        organization_id=org_id,
        supplier_name=supplier,
        supplier_code=code,
        sku_id=sku.id,
    ))
    db.flush()


def _pair(db, org_id) -> tuple[SKU, SKU]:
    doos = SKU(sku_code="BARB-DOOS", name="Barbebelle doos", organization_id=org_id)
    fles = SKU(
        sku_code="BARB-FLES",
        name="Barbebelle fles",
        organization_id=org_id,
        is_bottle=True,
    )
    db.add_all([doos, fles])
    db.flush()
    return doos, fles


def test_case_and_bottle_are_offered_side_by_side(client, db, owner_token, owner_user):
    org_id = owner_user.organization_id
    doos, fles = _pair(db, org_id)
    _link(db, org_id, doos)
    _link(db, org_id, fles)
    db.commit()

    resp = _preview(client, owner_token, _extracted())

    assert resp.status_code == 200
    line = resp.json()["lines"][0]
    # The most recently confirmed link is proposed…
    assert line["matched_sku_code"] == "BARB-FLES"
    assert line["match_source"] == "supplier_mapping"
    # …and the other product rides along so switching is one click.
    assert {c["sku_code"] for c in line["candidate_matches"]} == {"BARB-DOOS", "BARB-FLES"}
    assert {c["is_bottle"] for c in line["candidate_matches"]} == {True, False}


def test_a_single_link_still_matches_without_offering_a_choice(client, db, owner_token, owner_user):
    org_id = owner_user.organization_id
    doos, _ = _pair(db, org_id)
    _link(db, org_id, doos)
    db.commit()

    line = _preview(client, owner_token, _extracted()).json()["lines"][0]

    assert line["matched_sku_code"] == "BARB-DOOS"
    assert [c["sku_code"] for c in line["candidate_matches"]] == ["BARB-DOOS"]


def test_products_learned_under_another_spelling_are_offered_instead_of_dropped(
    client, db, owner_token, owner_user
):
    """The old behaviour left such a line unmatched with nothing to click."""
    org_id = owner_user.organization_id
    doos, fles = _pair(db, org_id)
    _link(db, org_id, doos, supplier="ANFORS-IMPERIAL")
    _link(db, org_id, fles, supplier="ANFORS-IMPERIAL")
    db.commit()

    line = _preview(client, owner_token, _extracted()).json()["lines"][0]

    assert line["matched_sku_id"] is None
    assert line["needs_confirmation"] is True
    assert {c["sku_code"] for c in line["candidate_matches"]} == {"BARB-DOOS", "BARB-FLES"}


def test_the_exact_supplier_name_wins_over_another_spelling(client, db, owner_token, owner_user):
    org_id = owner_user.organization_id
    doos, fles = _pair(db, org_id)
    _link(db, org_id, doos, supplier="ANFORS")
    _link(db, org_id, fles, supplier="ANFORS-IMPERIAL")
    db.commit()

    line = _preview(client, owner_token, _extracted()).json()["lines"][0]

    assert line["matched_sku_code"] == "BARB-DOOS"
    assert [c["sku_code"] for c in line["candidate_matches"]] == ["BARB-DOOS"]
