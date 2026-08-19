"""Tests for the webshop stock pool.

The webshop is a third physical pool next to the warehouse and the shop shelf.
It has to behave like the other two everywhere stock is read, adjusted or
counted — and the courier boundary has to hold for it just as it does for the
shop.
"""

from app.models import (
    SELLABLE_INVENTORY_LOCATIONS,
    VALID_INVENTORY_LOCATIONS,
    InventoryBalance,
    SKU,
)
from tests.conftest import auth_header


def _bottle(db, sample_org, code="FLES-WEB"):
    sku = SKU(
        sku_code=code,
        name="Webshopwijn",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=True,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def test_pool_catalog_names_all_three():
    assert VALID_INVENTORY_LOCATIONS == ("warehouse", "store", "webshop")


def test_sellable_pools_are_shop_and_webshop():
    """What the webshop may sell is defined once, not per screen."""
    assert SELLABLE_INVENTORY_LOCATIONS == ("store", "webshop")
    assert "warehouse" not in SELLABLE_INVENTORY_LOCATIONS


class TestWebshopOverview:
    def test_overview_accepts_the_webshop_pool(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=sample_org.id,
                inventory_location="webshop",
                quantity_on_hand=12,
            )
        )
        db.commit()

        resp = client.get(
            "/api/inventory/overview",
            params={"inventory_location": "webshop"},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        row = next(r for r in resp.json() if r["sku_id"] == sku.id)
        assert row["inventory_location"] == "webshop"
        assert row["quantity_on_hand"] == 12

    def test_every_pool_lists_the_same_products(
        self, client, db, owner_token, sample_org
    ):
        """A product without a balance in a pool still shows there, at zero.

        The shop and the webshop must offer the same catalog, otherwise a
        product can only ever be replenished into whichever pool happened to
        get stock first.
        """
        sku = _bottle(db, sample_org)
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=sample_org.id,
                inventory_location="store",
                quantity_on_hand=6,
            )
        )
        db.commit()

        listed = {}
        for pool in ("store", "webshop"):
            resp = client.get(
                "/api/inventory/overview",
                params={"inventory_location": pool},
                headers=auth_header(owner_token),
            )
            assert resp.status_code == 200
            listed[pool] = {r["sku_id"] for r in resp.json()}

        assert listed["store"] == listed["webshop"]
        assert sku.id in listed["webshop"]

    def test_balances_endpoint_accepts_the_webshop_pool(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=sample_org.id,
                inventory_location="webshop",
                quantity_on_hand=3,
            )
        )
        db.commit()

        resp = client.get(
            "/api/inventory",
            params={"inventory_location": "webshop"},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert [b["quantity_on_hand"] for b in resp.json()] == [3]


class TestWebshopAdjustAndCount:
    def test_adjust_books_into_the_webshop_pool(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)

        resp = client.post(
            "/api/inventory/adjust",
            json={
                "sku_id": sku.id,
                "quantity": 6,
                "inventory_location": "webshop",
                "note": "eerste vulling",
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["inventory_location"] == "webshop"

        balance = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == sku.id,
                InventoryBalance.inventory_location == "webshop",
            )
            .one()
        )
        assert balance.quantity_on_hand == 6

    def test_adjust_leaves_the_other_pools_alone(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=sample_org.id,
                inventory_location="store",
                quantity_on_hand=4,
            )
        )
        db.commit()

        client.post(
            "/api/inventory/adjust",
            json={"sku_id": sku.id, "quantity": 6, "inventory_location": "webshop"},
            headers=auth_header(owner_token),
        )

        store = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == sku.id,
                InventoryBalance.inventory_location == "store",
            )
            .one()
        )
        assert store.quantity_on_hand == 4

    def test_count_corrects_the_webshop_pool(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=sample_org.id,
                inventory_location="webshop",
                quantity_on_hand=10,
            )
        )
        db.commit()

        resp = client.post(
            "/api/inventory/count",
            json={
                "sku_id": sku.id,
                "counted_quantity": 7,
                "inventory_location": "webshop",
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["quantity"] == -3

    def test_movements_can_be_read_per_pool(
        self, client, db, owner_token, sample_org
    ):
        sku = _bottle(db, sample_org)
        client.post(
            "/api/inventory/adjust",
            json={"sku_id": sku.id, "quantity": 6, "inventory_location": "webshop"},
            headers=auth_header(owner_token),
        )

        resp = client.get(
            f"/api/inventory/{sku.id}/movements",
            params={"inventory_location": "webshop"},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert [m["quantity"] for m in resp.json()] == [6]

        resp = client.get(
            f"/api/inventory/{sku.id}/movements",
            params={"inventory_location": "store"},
            headers=auth_header(owner_token),
        )
        assert resp.json() == []


class TestCourierBoundary:
    def test_courier_may_not_read_the_webshop_pool(
        self, client, db, courier_token, sample_org
    ):
        _bottle(db, sample_org)

        resp = client.get(
            "/api/inventory/overview",
            params={
                "inventory_location": "webshop",
                "organization_id": sample_org.id,
            },
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_courier_may_not_adjust_the_webshop_pool(
        self, client, db, courier_token, sample_org
    ):
        sku = _bottle(db, sample_org)

        resp = client.post(
            "/api/inventory/adjust",
            json={
                "sku_id": sku.id,
                "quantity": 6,
                "inventory_location": "webshop",
                "organization_id": sample_org.id,
            },
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403


def test_unknown_pool_is_refused(client, db, owner_token, sample_org):
    sku = _bottle(db, sample_org)

    resp = client.post(
        "/api/inventory/adjust",
        json={"sku_id": sku.id, "quantity": 1, "inventory_location": "zolder"},
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 422
