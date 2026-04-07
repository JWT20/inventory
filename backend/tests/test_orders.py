"""Tests for order management."""

from tests.conftest import auth_header
from app.models import Customer, CustomerSKU, SKU, ReferenceImage


class TestCreateOrder:
    def test_owner_creates_order(self, client, db, owner_user, owner_token, sample_org):
        # Create customer and SKU
        customer = Customer(name="test klant", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-002", name="Test Wine 2")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 5}],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["organization_name"] == sample_org.name
        assert len(data["lines"]) == 1
        assert data["total_boxes"] == 5

    def test_customer_creates_order(self, client, db, customer_user, customer_token, sample_org):
        customer = Customer(name="klant record", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-003", name="Test Wine 3")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 2}],
            },
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created_by_name"] == "customer"

    def test_courier_cannot_create_order(self, client, courier_token):
        resp = client.post(
            "/api/orders",
            json={"lines": [{"customer_id": 1, "sku_id": 1, "quantity": 1}]},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_order_response_hides_prices_when_customer_disables_them(
        self, client, db, owner_user, owner_token, sample_org
    ):
        hidden_customer = Customer(
            name="verborgen klant",
            organization_id=sample_org.id,
            show_prices=False,
        )
        visible_customer = Customer(
            name="zichtbare klant",
            organization_id=sample_org.id,
            show_prices=True,
        )
        sku = SKU(sku_code="WINE-200", name="Prijs Test", default_price=10)
        db.add_all([hidden_customer, visible_customer, sku])
        db.commit()

        db.add_all([
            CustomerSKU(customer_id=hidden_customer.id, sku_id=sku.id, unit_price=12),
            CustomerSKU(customer_id=visible_customer.id, sku_id=sku.id, unit_price=11),
        ])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {"customer_id": hidden_customer.id, "sku_id": sku.id, "quantity": 2},
                    {"customer_id": visible_customer.id, "sku_id": sku.id, "quantity": 3},
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        hidden_line = next(l for l in data["lines"] if l["customer_id"] == hidden_customer.id)
        visible_line = next(l for l in data["lines"] if l["customer_id"] == visible_customer.id)

        assert hidden_line["show_prices"] is False
        assert hidden_line["effective_price"] is None
        assert hidden_line["line_total"] is None

        assert visible_line["show_prices"] is True
        assert visible_line["effective_price"] == 11.0
        assert visible_line["line_total"] == 33.0

        assert data["visible_total"] == 33.0
        assert data["hidden_lines_count"] == 1


class TestListOrders:
    def test_list_orders_unauthenticated(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code == 401

    def test_list_orders_empty(self, client, owner_token):
        resp = client.get("/api/orders", headers=auth_header(owner_token))
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetOrder:
    def test_get_nonexistent_order(self, client, owner_token):
        resp = client.get("/api/orders/9999", headers=auth_header(owner_token))
        assert resp.status_code == 404


class TestActivateOrder:
    def test_activate_order_without_images_fails(self, client, db, owner_user, owner_token, sample_org):
        customer = Customer(name="klant", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-004", name="Test Wine 4")
        db.add_all([customer, sku])
        db.commit()

        # Create order
        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 3}],
            },
            headers=auth_header(owner_token),
        )
        order_id = resp.json()["id"]

        # Try to activate without images
        resp = client.post(
            f"/api/orders/{order_id}/activate",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400
        assert "referentiebeelden" in resp.json()["detail"].lower()

    def test_courier_cannot_activate_order(self, client, db, owner_user, owner_token, courier_token, sample_org):
        customer = Customer(name="klant2", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-005", name="Test Wine 5")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )
        order_id = resp.json()["id"]

        resp = client.post(
            f"/api/orders/{order_id}/activate",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403


class TestSKUCodeGeneration:
    def test_sku_code_format(self):
        from app.schemas import generate_wine_sku_code

        attrs = {
            "producent": "Château Grand",
            "wijnaam": "Cru Rouge",
            "wijntype": "Rood",
            "volume": "750",
        }
        assert generate_wine_sku_code(attrs) == "CHAT-CRUR-ROO-750"

    def test_sku_code_with_spaces(self):
        from app.schemas import generate_wine_sku_code

        attrs = {
            "producent": "Domaine Belle",
            "wijnaam": "Blanc Premier",
            "wijntype": "Wit",
            "volume": "750",
        }
        assert generate_wine_sku_code(attrs) == "DOMA-BLAN-WIT-750"

    def test_display_name(self):
        from app.schemas import generate_wine_display_name

        attrs = {
            "producent": "Château Grand",
            "wijnaam": "Cru Rouge",
            "wijntype": "Rood",
            "volume": "750",
        }
        assert generate_wine_display_name(attrs) == "Château Grand Cru Rouge Rood"
