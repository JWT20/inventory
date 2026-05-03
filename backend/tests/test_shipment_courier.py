"""Couriers can confirm inbound shipments for any merchant."""

from app.models import SKU, InboundShipment, SupplierSKUMapping
from tests.conftest import auth_header


def test_courier_can_create_shipment_for_merchant(client, db, courier_token, sample_org):
    sku = SKU(sku_code="SKU-COUR-1", name="Courier wine", organization_id=sample_org.id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(courier_token),
        json={
            "organization_id": sample_org.id,
            "supplier_name": "Anfors",
            "reference": "PKB-COUR-1",
            "lines": [{"sku_id": sku.id, "quantity": 3, "supplier_code": "SUP-1"}],
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["organization_id"] == sample_org.id

    mapping = (
        db.query(SupplierSKUMapping)
        .filter(SupplierSKUMapping.organization_id == sample_org.id)
        .first()
    )
    assert mapping is not None
    assert mapping.sku_id == sku.id


def test_courier_create_shipment_requires_organization_id(client, db, courier_token, sample_org):
    sku = SKU(sku_code="SKU-COUR-2", name="Wine", organization_id=sample_org.id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(courier_token),
        json={
            "supplier_name": "Anfors",
            "reference": "PKB-COUR-2",
            "lines": [{"sku_id": sku.id, "quantity": 1, "supplier_code": "SUP-2"}],
        },
    )

    assert resp.status_code == 400
    assert "organization_id" in resp.json()["detail"]


def test_courier_create_shipment_rejects_unknown_org(client, db, courier_token, sample_org):
    sku = SKU(sku_code="SKU-COUR-3", name="Wine", organization_id=sample_org.id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(courier_token),
        json={
            "organization_id": 99999,
            "lines": [{"sku_id": sku.id, "quantity": 1, "supplier_code": "SUP-3"}],
        },
    )

    assert resp.status_code == 404


def test_courier_can_book_shipment(client, db, courier_token, sample_org):
    sku = SKU(sku_code="SKU-COUR-4", name="Wine", organization_id=sample_org.id)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    create_resp = client.post(
        "/api/shipments",
        headers=auth_header(courier_token),
        json={
            "organization_id": sample_org.id,
            "supplier_name": "Anfors",
            "reference": "PKB-COUR-4",
            "lines": [{"sku_id": sku.id, "quantity": 5, "supplier_code": "SUP-4"}],
        },
    )
    assert create_resp.status_code == 201
    shipment_id = create_resp.json()["id"]

    book_resp = client.post(
        f"/api/shipments/{shipment_id}/book",
        headers=auth_header(courier_token),
    )
    assert book_resp.status_code == 200, book_resp.text
    assert book_resp.json()["status"] == "booked"

    shipment = db.get(InboundShipment, shipment_id)
    assert shipment is not None
    assert shipment.booked_by is not None


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


def test_owner_create_shipment_still_uses_own_org(client, db, owner_token, owner_user):
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


def test_customer_cannot_create_shipment(client, customer_token, sample_org):
    resp = client.post(
        "/api/shipments",
        headers=auth_header(customer_token),
        json={
            "organization_id": sample_org.id,
            "lines": [],
        },
    )
    assert resp.status_code == 403
