from app.models import SKU, SupplierSKUMapping
from tests.conftest import auth_header


def test_create_shipment_persists_supplier_mappings(client, db, owner_token, owner_user):
    sku = SKU(
        sku_code="SKU-100",
        name="Shipment SKU",
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
            "reference": "PKB-555",
            "lines": [{"sku_id": sku.id, "quantity": 4, "supplier_code": "SUP-100"}],
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["lines"][0]["supplier_code"] == "SUP-100"

    mapping = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == owner_user.organization_id,
            SupplierSKUMapping.supplier_name == "Anfors",
            SupplierSKUMapping.supplier_code == "SUP-100",
        )
        .first()
    )
    assert mapping is not None
    assert mapping.sku_id == sku.id
