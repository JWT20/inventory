"""The advice app sells the shop and the webshop as one pool.

Two physical shelves, one thing the webshop can sell. So the feed can report
them added up, and a hold may span both — but every bottle has to be given back
to the shelf it was taken from, or the two counts drift apart.
"""

from app.config import settings
from app.models import AdviceReservation, InventoryBalance, SKU, StockMovement


API_KEY = "test-advice-write-key"
STOCK_KEY = "test-advice-stock-key"
RESERVATIONS_URL = "/api/integrations/advice/reservations"
STOCK_URL = "/api/integrations/advice/stock"


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_sales_api_key", API_KEY)
    monkeypatch.setattr(settings, "advice_stock_api_key", STOCK_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers(key: str = API_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _bottle(db, org, product_id: str, *, store=0, webshop=0, warehouse=0) -> SKU:
    sku = SKU(
        sku_code=f"{product_id.upper()}-FLES",
        name=product_id,
        organization_id=org.id,
        is_bottle=True,
        source_product_id=product_id,
    )
    db.add(sku)
    db.flush()
    for location, quantity in (
        ("store", store),
        ("webshop", webshop),
        ("warehouse", warehouse),
    ):
        if quantity:
            db.add(
                InventoryBalance(
                    sku_id=sku.id,
                    organization_id=org.id,
                    inventory_location=location,
                    quantity_on_hand=quantity,
                )
            )
    db.commit()
    return sku


def _balance(db, sku_id, location):
    return (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku_id, inventory_location=location)
        .one_or_none()
    )


def _payload(**overrides) -> dict:
    payload = {
        "external_order_id": "order_123",
        "fulfillment_method": "dockscan",
        "inventory_location": "webshop",
        "lines": [{"source_product_id": "prd_a", "quantity": 4}],
    }
    payload.update(overrides)
    return payload


class TestStockFeed:
    def test_sellable_adds_shop_and_webshop(self, client, db, sample_org, monkeypatch):
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", store=3, webshop=7, warehouse=99)

        resp = client.get(
            STOCK_URL,
            params={"inventory_location": "sellable"},
            headers=_headers(STOCK_KEY),
        )

        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["quantity_available"] == 10
        assert (item["quantity_store"], item["quantity_webshop"]) == (3, 7)

    def test_sellable_ignores_the_warehouse(self, client, db, sample_org, monkeypatch):
        """A bottle in the magazijn is not on any shelf a customer buys from."""
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", warehouse=50)

        resp = client.get(
            STOCK_URL,
            params={"inventory_location": "sellable"},
            headers=_headers(STOCK_KEY),
        )

        assert resp.json()["items"][0]["quantity_available"] == 0

    def test_single_pool_requests_still_work(self, client, db, sample_org, monkeypatch):
        """The feed is live; the old calls keep meaning exactly what they did."""
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", store=3, webshop=7, warehouse=99)

        for pool, expected in (("store", 3), ("webshop", 7), ("warehouse", 99)):
            resp = client.get(
                STOCK_URL,
                params={"inventory_location": pool},
                headers=_headers(STOCK_KEY),
            )
            assert resp.json()["items"][0]["quantity_available"] == expected

    def test_split_is_reported_alongside_every_pool(
        self, client, db, sample_org, monkeypatch
    ):
        """Where a bottle stands, without a second call."""
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", store=3, webshop=7)

        resp = client.get(
            STOCK_URL,
            params={"inventory_location": "webshop"},
            headers=_headers(STOCK_KEY),
        )

        item = resp.json()["items"][0]
        assert (item["quantity_store"], item["quantity_webshop"]) == (3, 7)

    def test_reservations_lower_what_is_sellable(
        self, client, db, sample_org, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", webshop=10)
        client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        resp = client.get(
            STOCK_URL,
            params={"inventory_location": "sellable"},
            headers=_headers(STOCK_KEY),
        )

        assert resp.json()["items"][0]["quantity_available"] == 6


class TestHoldSpansBothShelves:
    def test_delivery_takes_the_webshop_first(
        self, client, db, sample_org, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=10, webshop=10)

        resp = client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        assert resp.status_code == 200, resp.text
        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_reserved == 4
        assert _balance(db, sku.id, "store").quantity_reserved == 0

    def test_pickup_takes_the_shop_first(self, client, db, sample_org, monkeypatch):
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=10, webshop=10)

        resp = client.post(
            RESERVATIONS_URL,
            json=_payload(fulfillment_method="pickup", inventory_location="store"),
            headers=_headers(),
        )

        assert resp.status_code == 200, resp.text
        db.expire_all()
        assert _balance(db, sku.id, "store").quantity_reserved == 4
        assert _balance(db, sku.id, "webshop").quantity_reserved == 0

    def test_a_short_shelf_spills_over_to_the_other(
        self, client, db, sample_org, monkeypatch
    ):
        """Three on the webshop shelf and one in the shop still serves four."""
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=1, webshop=3)

        resp = client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        assert resp.status_code == 200, resp.text
        assert resp.json()["lines"] == [
            {"source_product_id": "prd_a", "sku_code": "PRD_A-FLES", "quantity": 4}
        ]
        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_reserved == 3
        assert _balance(db, sku.id, "store").quantity_reserved == 1

    def test_both_shelves_together_falling_short_reserves_nothing(
        self, client, db, sample_org, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=1, webshop=1, warehouse=99)

        resp = client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        assert resp.status_code == 409
        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_reserved == 0
        assert _balance(db, sku.id, "store").quantity_reserved == 0
        assert db.query(AdviceReservation).count() == 0

    def test_collect_settles_each_shelf_it_took_from(
        self, client, db, sample_org, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=1, webshop=3)
        client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        collected = client.post(
            f"{RESERVATIONS_URL}/order_123/collect", headers=_headers()
        )

        assert collected.status_code == 200, collected.text
        db.expire_all()
        webshop = _balance(db, sku.id, "webshop")
        store = _balance(db, sku.id, "store")
        assert (webshop.quantity_on_hand, webshop.quantity_reserved) == (0, 0)
        assert (store.quantity_on_hand, store.quantity_reserved) == (0, 0)
        movements = {
            (row.inventory_location, row.quantity)
            for row in db.query(StockMovement).filter_by(sku_id=sku.id)
        }
        assert movements == {("webshop", -3), ("store", -1)}

    def test_release_gives_every_bottle_back_to_its_own_shelf(
        self, client, db, sample_org, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, "prd_a", store=1, webshop=3)
        client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        released = client.post(
            f"{RESERVATIONS_URL}/order_123/release", headers=_headers()
        )

        assert released.status_code == 200, released.text
        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_reserved == 0
        assert _balance(db, sku.id, "store").quantity_reserved == 0
        assert _balance(db, sku.id, "webshop").quantity_on_hand == 3
        assert _balance(db, sku.id, "store").quantity_on_hand == 1
        assert db.query(StockMovement).count() == 0

    def test_a_retry_of_a_split_hold_is_still_the_same_order(
        self, client, db, sample_org, monkeypatch
    ):
        """The caller ordered four bottles; two rows must not read as a change."""
        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", store=1, webshop=3)
        client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        retry = client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        assert retry.status_code == 200, retry.text
        assert retry.json()["duplicate"] is True

    def test_the_merchant_sees_which_shelf_holds_what(
        self, client, db, sample_org, owner_token, monkeypatch
    ):
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", store=1, webshop=3)
        client.post(RESERVATIONS_URL, json=_payload(), headers=_headers())

        resp = client.get(
            "/api/advice-reservations", headers=auth_header(owner_token)
        )

        assert resp.status_code == 200, resp.text
        lines = resp.json()[0]["lines"]
        assert {(l["inventory_location"], l["quantity"]) for l in lines} == {
            ("store", 1),
            ("webshop", 3),
        }
        assert resp.json()[0]["total_quantity"] == 4


class TestDeliveryOrders:
    def test_a_delivery_order_defaults_to_the_webshop_pool(
        self, client, db, sample_org, monkeypatch
    ):
        from app.models import Order

        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", webshop=10)
        _observing_advice_channel(db, sample_org)

        resp = client.post(
            "/api/integrations/advice/orders",
            json=_delivery_payload(),
            headers=_headers(),
        )

        assert resp.status_code == 200, resp.text
        order = db.query(Order).filter_by(external_id="adv_order_1").one()
        assert order.inventory_location == "webshop"

    def test_the_old_warehouse_route_is_still_accepted(
        self, client, db, sample_org, monkeypatch
    ):
        """Both sides deploy separately; the live caller must not start failing."""
        from app.models import Order

        _configure(monkeypatch, sample_org.id)
        _bottle(db, sample_org, "prd_a", webshop=10)
        _observing_advice_channel(db, sample_org)

        resp = client.post(
            "/api/integrations/advice/orders",
            json=_delivery_payload(inventory_location="warehouse"),
            headers=_headers(),
        )

        assert resp.status_code == 200, resp.text
        order = db.query(Order).filter_by(external_id="adv_order_1").one()
        assert order.inventory_location == "warehouse"


def _observing_advice_channel(db, org):
    from app.models import ChannelConnection
    from app.services.advice_channel import ADVICE_CHANNEL

    db.add(
        ChannelConnection(
            organization_id=org.id,
            channel=ADVICE_CHANNEL,
            mode="observe",
            status="active",
        )
    )
    db.commit()


def _delivery_payload(**overrides) -> dict:
    payload = {
        "external_order_id": "adv_order_1",
        "order_reference": "JUR-2026-000123",
        "delivery_address": {
            "recipient_name": "Jan Jansen",
            "street": "Dorpsstraat",
            "house_number": "1",
            "postal_code": "1234AB",
            "city": "Utrecht",
            "country": "NL",
        },
        "lines": [{"source_product_id": "prd_a", "quantity": 2}],
    }
    payload.update(overrides)
    return payload
