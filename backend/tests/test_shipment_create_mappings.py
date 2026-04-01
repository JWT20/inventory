from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from app.models import SKU, SupplierSKUMapping
from app.routers.inventory import _upsert_supplier_mapping
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
            "supplier_name": " Anfors ",
            "reference": "PKB-555",
            "lines": [{"sku_id": sku.id, "quantity": 4, "supplier_code": "sup-100"}],
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["lines"][0]["supplier_code"] == "SUP-100"

    mapping = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == owner_user.organization_id,
            SupplierSKUMapping.supplier_name == "ANFORS",
            SupplierSKUMapping.supplier_code == "SUP-100",
        )
        .first()
    )
    assert mapping is not None
    assert mapping.sku_id == sku.id


def test_create_shipment_updates_existing_mapping(client, db, owner_token, owner_user):
    sku_old = SKU(sku_code="SKU-OLD", name="Old", organization_id=owner_user.organization_id)
    sku_new = SKU(sku_code="SKU-NEW", name="New", organization_id=owner_user.organization_id)
    db.add_all([sku_old, sku_new])
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="SUP-999",
        sku_id=sku_old.id,
    ))
    db.commit()

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "anfors",
            "reference": "PKB-556",
            "lines": [{"sku_id": sku_new.id, "quantity": 2, "supplier_code": "sup-999"}],
        },
    )
    assert resp.status_code == 201

    mapping = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == owner_user.organization_id,
            SupplierSKUMapping.supplier_name == "ANFORS",
            SupplierSKUMapping.supplier_code == "SUP-999",
        )
        .one()
    )
    assert mapping.sku_id == sku_new.id


def test_create_shipment_case_collision_reuses_single_mapping(client, db, owner_token, owner_user):
    sku = SKU(sku_code="SKU-COLLIDE", name="Collide", organization_id=owner_user.organization_id)
    db.add(sku)
    db.flush()
    db.add(SupplierSKUMapping(
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="SUP-COLLIDE",
        sku_id=sku.id,
    ))
    db.commit()

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": " anFors ",
            "reference": "PKB-557",
            "lines": [{"sku_id": sku.id, "quantity": 1, "supplier_code": "sup-collide"}],
        },
    )
    assert resp.status_code == 201

    count = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == owner_user.organization_id,
            SupplierSKUMapping.supplier_name == "ANFORS",
            SupplierSKUMapping.supplier_code == "SUP-COLLIDE",
        )
        .count()
    )
    assert count == 1


def test_upsert_supplier_mapping_handles_concurrent_insert(db, owner_user):
    sku_old = SKU(sku_code="SKU-C1", name="C1", organization_id=owner_user.organization_id)
    sku_new = SKU(sku_code="SKU-C2", name="C2", organization_id=owner_user.organization_id)
    db.add_all([sku_old, sku_new])
    db.flush()
    existing = SupplierSKUMapping(
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="SUP-CONCURRENT",
        sku_id=sku_old.id,
    )
    db.add(existing)
    db.commit()

    @contextmanager
    def _raise_integrity():
        raise IntegrityError("insert", {}, Exception("duplicate"))
        yield

    db.begin_nested = _raise_integrity  # type: ignore[method-assign]
    _upsert_supplier_mapping(
        db,
        organization_id=owner_user.organization_id,
        supplier_name="ANFORS",
        supplier_code="SUP-CONCURRENT",
        sku_id=sku_new.id,
    )
    db.commit()
    db.refresh(existing)
    assert existing.sku_id == sku_new.id
