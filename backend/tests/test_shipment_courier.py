"""Inbound is per merchant: couriers can no longer create/book shipments.

Couriers retain access to the receiving concept-product endpoint, which is a
separate flow from inbound.
"""

from app.models import SKU, InboundShipment, InboundShipmentLine, Organization
from tests.conftest import auth_header


def _draft_shipment(db, org_id, sku_id, *, reference="PKB-DRAFT"):
    shipment = InboundShipment(
        organization_id=org_id,
        supplier_name="Anfors",
        reference=reference,
        status="draft",
    )
    db.add(shipment)
    db.flush()
    db.add(InboundShipmentLine(shipment_id=shipment.id, sku_id=sku_id, quantity=2))
    db.commit()
    db.refresh(shipment)
    return shipment


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

    # Inbound is per merchant; couriers are rejected by the access dependency.
    assert resp.status_code == 403, resp.text


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


def test_courier_cannot_book_shipment(client, db, courier_token, owner_user):
    sku = SKU(sku_code="SKU-BOOK-1", name="Wine", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, owner_user.organization_id, sku.id, reference="PKB-BOOK-1")

    resp = client.post(
        f"/api/shipments/{shipment.id}/book",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403, resp.text

    # Stock must not have moved.
    assert db.get(InboundShipment, shipment.id).status == "draft"


def test_courier_cannot_delete_shipment(client, db, courier_token, owner_user):
    sku = SKU(sku_code="SKU-DEL-1", name="Wine", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, owner_user.organization_id, sku.id, reference="PKB-DEL-1")

    resp = client.delete(
        f"/api/shipments/{shipment.id}",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403, resp.text
    assert db.get(InboundShipment, shipment.id) is not None


def test_owner_cannot_book_other_org_shipment(client, db, owner_token):
    other_org = Organization(name="Andere", slug="andere-org")
    db.add(other_org)
    db.flush()
    sku = SKU(sku_code="SKU-OTHER-1", name="Wine", organization_id=other_org.id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, other_org.id, sku.id, reference="PKB-OTHER-1")

    resp = client.post(
        f"/api/shipments/{shipment.id}/book",
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 404, resp.text
    assert db.get(InboundShipment, shipment.id).status == "draft"


def test_owner_can_book_own_shipment(client, db, owner_token, owner_user):
    sku = SKU(sku_code="SKU-OWNBOOK-1", name="Wine", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, owner_user.organization_id, sku.id, reference="PKB-OWNBOOK-1")

    resp = client.post(
        f"/api/shipments/{shipment.id}/book",
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "booked"


def test_owner_can_read_own_shipment(client, db, owner_token, owner_user):
    sku = SKU(sku_code="SKU-READ-1", name="Wine", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, owner_user.organization_id, sku.id, reference="PKB-READ-1")

    resp = client.get(
        f"/api/shipments/{shipment.id}",
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == shipment.id


def test_owner_cannot_read_other_org_shipment(client, db, owner_token):
    other_org = Organization(name="Andere lees", slug="andere-lees-org")
    db.add(other_org)
    db.flush()
    sku = SKU(sku_code="SKU-READ-OTHER", name="Wine", organization_id=other_org.id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, other_org.id, sku.id, reference="PKB-READ-OTHER")

    resp = client.get(
        f"/api/shipments/{shipment.id}",
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 404, resp.text


def test_courier_cannot_read_shipment(client, db, courier_token, owner_user):
    sku = SKU(sku_code="SKU-READ-COUR", name="Wine", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    shipment = _draft_shipment(db, owner_user.organization_id, sku.id, reference="PKB-READ-COUR")

    resp = client.get(
        f"/api/shipments/{shipment.id}",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 404, resp.text
