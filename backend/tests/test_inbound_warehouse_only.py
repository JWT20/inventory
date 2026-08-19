"""Inbound goods always land in the warehouse.

A delivery note covers a whole document at once, so a destination choice on it
could never be made per product — and everything physically arrives at the
warehouse anyway. What goes to the shop or the webshop is decided afterwards, by
moving or replenishing it.
"""

from app.models import SKU, InboundShipment, InventoryBalance
from tests.conftest import auth_header


def _sku(db, owner_user, code="INBOUND-LOC"):
    sku = SKU(
        sku_code=code,
        name="Inboundwijn",
        organization_id=owner_user.organization_id,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def test_shipment_defaults_to_the_warehouse(client, db, owner_token, owner_user):
    sku = _sku(db, owner_user)

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Vojacek",
            "reference": "PKB-DEFAULT",
            "lines": [{"sku_id": sku.id, "quantity": 2}],
        },
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["inventory_location"] == "warehouse"


def test_booking_lands_in_the_warehouse_pool(client, db, owner_token, owner_user):
    sku = _sku(db, owner_user, code="INBOUND-BOOK")

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Vojacek",
            "reference": "PKB-BOEK",
            "lines": [{"sku_id": sku.id, "quantity": 3}],
        },
    )
    assert created.status_code == 201, created.text
    booked = client.post(
        f'/api/shipments/{created.json()["id"]}/book',
        headers=auth_header(owner_token),
    )
    assert booked.status_code == 200, booked.text

    balances = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).all()
    )
    assert [(b.inventory_location, b.quantity_on_hand) for b in balances] == [
        ("warehouse", 3)
    ]


def test_shop_as_destination_is_refused(client, db, owner_token, owner_user):
    sku = _sku(db, owner_user, code="INBOUND-STORE")

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Vojacek",
            "reference": "PKB-WINKEL",
            "inventory_location": "store",
            "lines": [{"sku_id": sku.id, "quantity": 2}],
        },
    )

    assert resp.status_code == 422
    assert db.query(InboundShipment).count() == 0


def test_explicit_warehouse_still_accepted(client, db, owner_token, owner_user):
    """An older caller that still sends the field keeps working."""
    sku = _sku(db, owner_user, code="INBOUND-EXPLICIT")

    resp = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Vojacek",
            "reference": "PKB-EXPLICIET",
            "inventory_location": "warehouse",
            "lines": [{"sku_id": sku.id, "quantity": 1}],
        },
    )

    assert resp.status_code == 201, resp.text


def test_older_shop_shipments_stay_readable(client, db, owner_token, owner_user):
    """Pakbonnen booked to the shop before this change must still open."""
    shipment = InboundShipment(
        organization_id=owner_user.organization_id,
        supplier_name="Vojacek",
        reference="PKB-OUD-WINKEL",
        status="booked",
        inventory_location="store",
    )
    db.add(shipment)
    db.commit()

    resp = client.get(
        f"/api/shipments/{shipment.id}", headers=auth_header(owner_token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["inventory_location"] == "store"
