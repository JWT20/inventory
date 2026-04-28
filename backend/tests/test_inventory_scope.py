"""Tests for inventory merchant scope and reserved quantity behavior."""

from app.models import Customer, CustomerSKU, InventoryBalance, SKU
from tests.conftest import auth_header


def _make_stock(db, sample_org, *, on_hand=10, reserved=0):
    sku = SKU(
        sku_code=f"WINE-STOCK-{on_hand}-{reserved}",
        name="Voorraadwijn",
        organization_id=sample_org.id,
        default_price=12,
    )
    customer = Customer(name="prijs klant", organization_id=sample_org.id)
    db.add_all([sku, customer])
    db.commit()
    db.add_all([
        CustomerSKU(customer_id=customer.id, sku_id=sku.id, unit_price=11),
        InventoryBalance(
            sku_id=sku.id,
            organization_id=sample_org.id,
            quantity_on_hand=on_hand,
            quantity_reserved=reserved,
        ),
    ])
    db.commit()
    db.refresh(sku)
    return sku


class TestInventoryScope:
    def test_admin_inventory_overview_requires_organization(
        self, client, admin_token
    ):
        resp = client.get(
            "/api/inventory/overview",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 400

    def test_courier_inventory_overview_requires_organization(
        self, client, courier_token
    ):
        resp = client.get(
            "/api/inventory/overview",
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 400

    def test_admin_inventory_overview_scopes_to_merchant(
        self, client, db, admin_token, sample_org
    ):
        sku = _make_stock(db, sample_org, on_hand=8, reserved=3)

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert [row["sku_id"] for row in data] == [sku.id]
        assert data[0]["quantity_on_hand"] == 8
        assert data[0]["quantity_reserved"] == 3
        assert data[0]["quantity_available"] == 5
        assert len(data[0]["customer_prices"]) == 1

    def test_courier_inventory_overview_hides_customer_prices(
        self, client, db, courier_token, sample_org
    ):
        _make_stock(db, sample_org, on_hand=8, reserved=3)

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}",
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 200
        assert resp.json()[0]["customer_prices"] == []


class TestReservedInventory:
    def test_adjust_cannot_drop_stock_below_reserved(
        self, client, db, owner_token, sample_org
    ):
        sku = _make_stock(db, sample_org, on_hand=5, reserved=3)

        resp = client.post(
            "/api/inventory/adjust",
            json={"sku_id": sku.id, "quantity": -3, "note": "te laag"},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 409

    def test_count_cannot_drop_stock_below_reserved(
        self, client, db, owner_token, sample_org
    ):
        sku = _make_stock(db, sample_org, on_hand=5, reserved=3)

        resp = client.post(
            "/api/inventory/count",
            json={"sku_id": sku.id, "counted_quantity": 2, "note": "telling"},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 409
