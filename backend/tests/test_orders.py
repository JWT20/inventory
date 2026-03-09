"""Tests for order management and CSV upload."""

import io

from tests.conftest import auth_header


VALID_CSV = """klant;producent;wijnaam;type;jaargang;volume;aantal
Restaurant De Zwaan;Château Grand;Cru Rouge;Rood;2019;750;6
Wijnbar Zuid;Domaine Belle;Blanc Premier;Wit;2021;750;12
Restaurant De Zwaan;Château Grand;Cru Rouge;Rood;2019;750;4
"""

CSV_MISSING_COLUMN = """producent;wijnaam;type;jaargang;volume
Château Grand;Cru Rouge;Rood;2019;750
"""

CSV_INVALID_AANTAL = """klant;producent;wijnaam;type;jaargang;volume;aantal
Restaurant De Zwaan;Château Grand;Cru Rouge;Rood;2019;750;0
"""


class TestCSVUpload:
    def test_upload_valid_csv(self, client, merchant_token):
        resp = client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        # 2 unique SKUs (Château Grand Cru Rouge appears twice, quantities summed)
        assert len(data["new_skus"]) == 2
        assert len(data["matched_skus"]) == 0
        assert len(data["errors"]) == 0

    def test_upload_csv_matches_existing_sku(self, client, db, merchant_token):
        from app.models import SKU

        # Pre-create a SKU that matches the CSV row
        csv_row_sku_code = "CHAT-CRUR-ROO-2019-750"
        sku = SKU(sku_code=csv_row_sku_code, name="Existing Wine")
        db.add(sku)
        db.commit()

        resp = client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        matched_codes = [s["sku_code"] for s in data["matched_skus"]]
        assert csv_row_sku_code in matched_codes
        # The other SKU is new
        assert len(data["new_skus"]) == 1

    def test_upload_csv_missing_column(self, client, merchant_token):
        resp = client.post(
            "/api/orders/upload-csv",
            files={
                "file": (
                    "bad.csv",
                    io.BytesIO(CSV_MISSING_COLUMN.encode()),
                    "text/csv",
                )
            },
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400
        assert "aantal" in resp.json()["detail"].lower()

    def test_upload_csv_invalid_aantal(self, client, merchant_token):
        resp = client.post(
            "/api/orders/upload-csv",
            files={
                "file": (
                    "bad.csv",
                    io.BytesIO(CSV_INVALID_AANTAL.encode()),
                    "text/csv",
                )
            },
            headers=auth_header(merchant_token),
        )
        # Should fail because no valid rows
        assert resp.status_code == 400

    def test_courier_cannot_upload_csv(self, client, courier_token):
        resp = client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_non_csv_rejected(self, client, merchant_token):
        resp = client.post(
            "/api/orders/upload-csv",
            files={
                "file": (
                    "data.json",
                    io.BytesIO(b'{"not": "csv"}'),
                    "application/json",
                )
            },
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400


class TestListOrders:
    def test_list_orders(self, client, merchant_token):
        # Create an order via CSV first
        client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        resp = client.get("/api/orders", headers=auth_header(merchant_token))
        assert resp.status_code == 200
        orders = resp.json()
        assert len(orders) >= 1
        assert orders[0]["status"] == "pending_images"

    def test_list_orders_unauthenticated(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code == 401


class TestGetOrder:
    def test_get_order(self, client, merchant_token):
        client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        orders = client.get(
            "/api/orders", headers=auth_header(merchant_token)
        ).json()
        order_id = orders[0]["id"]

        resp = client.get(
            f"/api/orders/{order_id}", headers=auth_header(merchant_token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == order_id
        assert len(data["lines"]) == 2  # 2 unique SKUs

    def test_get_nonexistent_order(self, client, merchant_token):
        resp = client.get(
            "/api/orders/9999", headers=auth_header(merchant_token)
        )
        assert resp.status_code == 404


class TestActivateOrder:
    def test_activate_order_without_images_fails(self, client, merchant_token):
        client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        orders = client.get(
            "/api/orders", headers=auth_header(merchant_token)
        ).json()
        order_id = orders[0]["id"]

        resp = client.post(
            f"/api/orders/{order_id}/activate",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400
        assert "referentiebeelden" in resp.json()["detail"].lower()

    def test_courier_cannot_activate_order(self, client, courier_token, merchant_token):
        client.post(
            "/api/orders/upload-csv",
            files={"file": ("order.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_header(merchant_token),
        )
        orders = client.get(
            "/api/orders", headers=auth_header(merchant_token)
        ).json()
        order_id = orders[0]["id"]

        resp = client.post(
            f"/api/orders/{order_id}/activate",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403


class TestSKUCodeGeneration:
    def test_sku_code_format(self):
        from app.schemas import CSVRow

        row = CSVRow(
            klant="Restaurant De Zwaan",
            producent="Château Grand",
            wijnaam="Cru Rouge",
            type="Rood",
            jaargang="2019",
            volume="750",
            aantal=6,
        )
        assert row.sku_code == "CHAT-CRUR-ROO-2019-750"

    def test_sku_code_with_spaces(self):
        from app.schemas import CSVRow

        row = CSVRow(
            klant="Wijnbar Zuid",
            producent="Domaine Belle",
            wijnaam="Blanc Premier",
            type="Wit",
            jaargang="2021",
            volume="750",
            aantal=12,
        )
        assert row.sku_code == "DOMA-BLAN-WIT-2021-750"

    def test_display_name(self):
        from app.schemas import CSVRow

        row = CSVRow(
            klant="Restaurant De Zwaan",
            producent="Château Grand",
            wijnaam="Cru Rouge",
            type="Rood",
            jaargang="2019",
            volume="750",
            aantal=6,
        )
        assert row.display_name == "Château Grand Cru Rouge Rood 2019"
