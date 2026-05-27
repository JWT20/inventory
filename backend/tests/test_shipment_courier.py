"""Inbound is per merchant: couriers can no longer create/book shipments.

Couriers retain access to the receiving concept-product endpoint, which is a
separate flow from inbound.
"""

from app.models import SKU
from tests.conftest import auth_header


def test_courier_cannot_create_shipment(client, db, courier_token, sample_org):
    sku = SKU(sku_code="SKU-COUR-1", name="Courier wine", organization_id=sample_org.id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(courier_token),
        json={
            "supplier_name": "Anfors",
            "reference": "PKB-COUR-1",
            "lines": [{"sku_id": sku.id, "quantity": 3, "supplier_code": "SUP-1"}],
        },
    )

    # A courier has no own organization, so inbound is not available to them.
    assert resp.status_code == 400, resp.text


def test_courier_can_create_concept_product_for_merchant(client, db, courier_token, sample_org):
    resp = client.post(
        "/api/receiving/concept-product",
        headers=auth_header(courier_token),
        data={
            "supplier_code": "ANF-NEW-001",
            "description": "Concept wine",
            "organization_id": str(sample_org.id),
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sku_code"] == "ANF-NEW-001"
    assert body["active"] is False

    sku = db.query(SKU).filter(SKU.sku_code == "ANF-NEW-001").one()
    assert sku.organization_id == sample_org.id


def test_courier_concept_product_rejects_unknown_org(client, courier_token):
    resp = client.post(
        "/api/receiving/concept-product",
        headers=auth_header(courier_token),
        data={"supplier_code": "ANF-NEW-002", "organization_id": "99999"},
    )

    assert resp.status_code == 404


def test_owner_create_shipment_uses_own_org(client, db, owner_token, owner_user):
    sku = SKU(
        sku_code="SKU-OWN-1",
        name="Owner wine",
        organization_id=owner_user.organization_id,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Anfors",
            "reference": "PKB-OWN-1",
            "lines": [{"sku_id": sku.id, "quantity": 2, "supplier_code": "SUP-O1"}],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["organization_id"] == owner_user.organization_id


def test_customer_cannot_create_shipment(client, customer_token):
    resp = client.post(
        "/api/shipments",
        headers=auth_header(customer_token),
        json={"lines": []},
    )
    assert resp.status_code == 403
