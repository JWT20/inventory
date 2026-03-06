"""Tests for order CRUD endpoints."""

from tests.conftest import auth_header


class TestListOrders:
    def test_list_orders(self, client, courier_token, sample_order):
        resp = client.get("/api/orders", headers=auth_header(courier_token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["order_number"] == "ORD-001"
        assert data[0]["dock_location"] == "C1"

    def test_list_orders_filter_status(self, client, courier_token, sample_order):
        resp = client.get(
            "/api/orders", params={"status": "fulfilled"},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_list_orders_unauthenticated(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code == 401


class TestCreateOrder:
    def test_merchant_creates_order(self, client, merchant_token, sample_sku):
        resp = client.post("/api/orders", json={
            "order_number": "ORD-NEW",
            "customer_name": "Klant B",
            "dock_location": "C2",
            "lines": [{"sku_code": "WINE-001", "quantity": 5}],
        }, headers=auth_header(merchant_token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["order_number"] == "ORD-NEW"
        assert data["dock_location"] == "C2"
        assert data["status"] == "pending"
        assert len(data["lines"]) == 1
        assert data["lines"][0]["quantity"] == 5
        assert data["lines"][0]["received_quantity"] == 0

    def test_courier_cannot_create_order(self, client, courier_token, sample_sku):
        resp = client.post("/api/orders", json={
            "order_number": "ORD-X",
            "customer_name": "Nope",
            "lines": [{"sku_code": "WINE-001", "quantity": 1}],
        }, headers=auth_header(courier_token))
        assert resp.status_code == 403

    def test_duplicate_order_number(self, client, merchant_token, sample_order):
        resp = client.post("/api/orders", json={
            "order_number": "ORD-001",
            "customer_name": "Duplicate",
            "lines": [{"sku_code": "WINE-001", "quantity": 1}],
        }, headers=auth_header(merchant_token))
        assert resp.status_code == 400

    def test_unknown_sku_rejected(self, client, merchant_token):
        resp = client.post("/api/orders", json={
            "order_number": "ORD-BAD",
            "customer_name": "Test",
            "lines": [{"sku_code": "NONEXISTENT", "quantity": 1}],
        }, headers=auth_header(merchant_token))
        assert resp.status_code == 404

    def test_empty_lines_rejected(self, client, merchant_token):
        resp = client.post("/api/orders", json={
            "order_number": "ORD-EMPTY",
            "customer_name": "Test",
            "lines": [],
        }, headers=auth_header(merchant_token))
        assert resp.status_code == 422


class TestGetOrder:
    def test_get_order(self, client, courier_token, sample_order):
        resp = client.get(
            f"/api/orders/{sample_order.id}",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert resp.json()["order_number"] == "ORD-001"

    def test_get_order_not_found(self, client, courier_token):
        resp = client.get("/api/orders/9999", headers=auth_header(courier_token))
        assert resp.status_code == 404


class TestUpdateOrder:
    def test_update_dock_location(self, client, merchant_token, sample_order):
        resp = client.patch(
            f"/api/orders/{sample_order.id}",
            json={"dock_location": "C5"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        assert resp.json()["dock_location"] == "C5"

    def test_courier_cannot_update_order(self, client, courier_token, sample_order):
        resp = client.patch(
            f"/api/orders/{sample_order.id}",
            json={"dock_location": "C9"},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403


class TestUpdateOrderStatus:
    def test_update_status(self, client, courier_token, sample_order):
        resp = client.patch(
            f"/api/orders/{sample_order.id}/status",
            params={"status": "receiving"},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "receiving"

    def test_invalid_status_rejected(self, client, courier_token, sample_order):
        resp = client.patch(
            f"/api/orders/{sample_order.id}/status",
            params={"status": "invalid"},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 400


class TestDeleteOrder:
    def test_merchant_deletes_order(self, client, merchant_token, sample_order):
        resp = client.delete(
            f"/api/orders/{sample_order.id}",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 204

    def test_courier_cannot_delete_order(self, client, courier_token, sample_order):
        resp = client.delete(
            f"/api/orders/{sample_order.id}",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_delete_nonexistent_order(self, client, merchant_token):
        resp = client.delete(
            "/api/orders/9999",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 404
