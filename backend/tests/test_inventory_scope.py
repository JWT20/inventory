"""Tests for inventory merchant scope and reserved quantity behavior."""

from app.models import (
    Customer,
    CustomerSKU,
    InventoryBalance,
    Organization,
    ReferenceImage,
    SKU,
    Supplier,
)
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

    def test_inventory_overview_returns_done_thumbnail(
        self, client, db, admin_token, sample_org
    ):
        sku = _make_stock(db, sample_org, on_hand=8, reserved=3)
        db.add_all(
            [
                ReferenceImage(
                    sku_id=sku.id,
                    image_path="reference_images/pending.jpg",
                    processing_status="pending",
                ),
                ReferenceImage(
                    sku_id=sku.id,
                    image_path="reference_images/done.jpg",
                    processing_status="done",
                ),
            ]
        )
        db.commit()

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        assert resp.json()[0]["image_url"] == (
            "/api/thumbnails/112/reference_images/done.jpg"
        )

    def test_inventory_overview_includes_only_inactive_skus_with_stock(
        self, client, db, admin_token, sample_org
    ):
        active_zero = SKU(
            sku_code="ACTIVE-ZERO",
            name="Actief zonder voorraad",
            organization_id=sample_org.id,
            active=True,
        )
        inactive_stock = SKU(
            sku_code="INACTIVE-STOCK",
            name="Inactief met voorraad",
            organization_id=sample_org.id,
            active=False,
        )
        inactive_zero = SKU(
            sku_code="INACTIVE-ZERO",
            name="Inactief zonder voorraad",
            organization_id=sample_org.id,
            active=False,
        )
        db.add_all([active_zero, inactive_stock, inactive_zero])
        db.flush()
        db.add(
            InventoryBalance(
                sku_id=inactive_stock.id,
                organization_id=sample_org.id,
                quantity_on_hand=7,
                quantity_reserved=2,
            )
        )
        db.commit()

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        rows = {row["sku_code"]: row for row in resp.json()}
        assert set(rows) == {"ACTIVE-ZERO", "INACTIVE-STOCK"}
        assert rows["ACTIVE-ZERO"]["active"] is True
        assert rows["INACTIVE-STOCK"]["active"] is False
        assert rows["INACTIVE-STOCK"]["quantity_on_hand"] == 7

    def test_courier_inventory_overview_hides_prices(
        self, client, db, courier_token, sample_org
    ):
        _make_stock(db, sample_org, on_hand=8, reserved=3)

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}",
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["default_price"] is None
        assert row["customer_prices"] == []

    def test_inventory_overview_search_by_supplier_name(
        self, client, db, admin_token, sample_org
    ):
        """Typing a supplier name in the search box lists that supplier's wines."""
        supplier = Supplier(name="Domaine Leflaive", organization_id=sample_org.id)
        other_supplier = Supplier(name="Bodega Catena", organization_id=sample_org.id)
        db.add_all([supplier, other_supplier])
        db.commit()

        wine_from_supplier = SKU(
            sku_code="WINE-SUP-1",
            name="Puligny-Montrachet",
            organization_id=sample_org.id,
            supplier_id=supplier.id,
        )
        wine_other_supplier = SKU(
            sku_code="WINE-SUP-2",
            name="Malbec Reserva",
            organization_id=sample_org.id,
            supplier_id=other_supplier.id,
        )
        wine_no_supplier = SKU(
            sku_code="WINE-SUP-3",
            name="Naamloze wijn",
            organization_id=sample_org.id,
        )
        db.add_all([wine_from_supplier, wine_other_supplier, wine_no_supplier])
        db.commit()

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}&search=leflaive",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        codes = {row["sku_code"] for row in resp.json()}
        assert codes == {"WINE-SUP-1"}

    def test_inventory_overview_search_matches_name_or_supplier(
        self, client, db, admin_token, sample_org
    ):
        """A search term still matches product names, not only suppliers."""
        supplier = Supplier(name="Leflaive", organization_id=sample_org.id)
        db.add(supplier)
        db.commit()

        by_name = SKU(
            sku_code="WINE-NAME",
            name="Leflaive Bourgogne Blanc",
            organization_id=sample_org.id,
        )
        by_supplier = SKU(
            sku_code="WINE-LINK",
            name="Andere wijn",
            organization_id=sample_org.id,
            supplier_id=supplier.id,
        )
        db.add_all([by_name, by_supplier])
        db.commit()

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}&search=leflaive",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        codes = {row["sku_code"] for row in resp.json()}
        assert codes == {"WINE-NAME", "WINE-LINK"}


class TestInventoryOverviewEanSearch:
    """Barcode orgs look products up by scanning/typing the EAN."""

    def _make_barcode_sku(self, db, org, *, code, name, ean):
        sku = SKU(
            sku_code=code,
            name=name,
            organization_id=org.id,
            product_type="barcode",
            ean=ean,
        )
        db.add(sku)
        db.commit()
        return sku

    def test_search_by_full_ean(self, client, db, admin_token, sample_org):
        self._make_barcode_sku(
            db, sample_org, code="SOK-ZW-M", name="Wielersok zwart M",
            ean="8712345678906",
        )
        self._make_barcode_sku(
            db, sample_org, code="SOK-WI-M", name="Wielersok wit M",
            ean="4006381333931",
        )

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}"
            "&search=8712345678906",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        rows = resp.json()
        assert [row["sku_code"] for row in rows] == ["SOK-ZW-M"]
        assert rows[0]["ean"] == "8712345678906"

    def test_search_by_partial_ean(self, client, db, admin_token, sample_org):
        self._make_barcode_sku(
            db, sample_org, code="SOK-ZW-M", name="Wielersok zwart M",
            ean="8712345678906",
        )

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}&search=456789",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        assert [row["sku_code"] for row in resp.json()] == ["SOK-ZW-M"]

    def test_scanner_whitespace_is_trimmed(self, client, db, admin_token, sample_org):
        self._make_barcode_sku(
            db, sample_org, code="SOK-ZW-M", name="Wielersok zwart M",
            ean="8712345678906",
        )

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}"
            "&search=%208712345678906%20",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        assert [row["sku_code"] for row in resp.json()] == ["SOK-ZW-M"]

    def test_ean_search_stays_within_organization(
        self, client, db, admin_token, sample_org
    ):
        """The same EAN may exist at another merchant; it must not leak."""
        other_org = Organization(name="Andere Handel", slug="andere-handel")
        db.add(other_org)
        db.commit()
        db.refresh(other_org)
        self._make_barcode_sku(
            db, sample_org, code="SOK-MINE", name="Eigen sok",
            ean="8712345678906",
        )
        self._make_barcode_sku(
            db, other_org, code="SOK-THEIRS", name="Andermans sok",
            ean="8712345678906",
        )

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}"
            "&search=8712345678906",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        assert [row["sku_code"] for row in resp.json()] == ["SOK-MINE"]

    def test_vision_products_keep_null_ean(self, client, db, admin_token, sample_org):
        """Wines have no EAN, so the extra term can never match them."""
        db.add(SKU(sku_code="WINE-1", name="Wijntje", organization_id=sample_org.id))
        db.commit()

        resp = client.get(
            f"/api/inventory/overview?organization_id={sample_org.id}&search=871234",
            headers=auth_header(admin_token),
        )

        assert resp.status_code == 200
        assert resp.json() == []


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
