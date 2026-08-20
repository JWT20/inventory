"""A live advice order: the hold and the pick settle each other exactly once.

The bottles are held when the customer pays and taken off the shelf when the
courier picks. Both touch the same stock, so the whole point of these tests is
that it leaves once — off the shelf it was actually held on, even when the hold
was split over the shop and the webshop.
"""

import pytest

from app.config import settings
from app.models import (
    AdviceReservation,
    AdviceReservationLine,
    ChannelConnection,
    InventoryBalance,
    Order,
    ReferenceImage,
    SKU,
    StockMovement,
)
from app.services.booking import apply_booking, undo_booking


WRITE_KEY = "test-advice-write-key"
RESERVATIONS_URL = "/api/integrations/advice/reservations"
ORDERS_URL = "/api/integrations/advice/orders"

ADDRESS = {
    "recipient_name": "Anna de Vries",
    "street": "Turfsingel",
    "house_number": "8",
    "postal_code": "9712 KR",
    "city": "Groningen",
    "country": "NL",
}


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_sales_api_key", WRITE_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WRITE_KEY}"}


def _live(db, org) -> ChannelConnection:
    connection = ChannelConnection(
        organization_id=org.id, channel="advice", mode="live"
    )
    db.add(connection)
    db.commit()
    return connection


def _bottle(db, org, product_id="prd_a", *, store=0, webshop=0) -> SKU:
    sku = SKU(
        sku_code=f"{product_id.upper()}-FLES",
        name=product_id,
        organization_id=org.id,
        product_type="vision",
        is_bottle=True,
        source_product_id=product_id,
    )
    db.add(sku)
    db.flush()
    db.add(
        ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done")
    )
    for location, quantity in (("store", store), ("webshop", webshop)):
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
    db.refresh(sku)
    return sku


def _balance(db, sku_id, location):
    return (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku_id, inventory_location=location)
        .one_or_none()
    )


def _reserve(client, quantity=4, external="order_123"):
    return client.post(
        RESERVATIONS_URL,
        json={
            "external_order_id": external,
            "fulfillment_method": "dockscan",
            "inventory_location": "webshop",
            "lines": [{"source_product_id": "prd_a", "quantity": quantity}],
        },
        headers=_headers(),
    )


def _order(client, quantity=4, external="order_123"):
    return client.post(
        ORDERS_URL,
        json={
            "external_order_id": external,
            "order_reference": "JUR-2026-8CERZC",
            "delivery_address": dict(ADDRESS),
            "lines": [{"source_product_id": "prd_a", "quantity": quantity}],
        },
        headers=_headers(),
    )


def _pick(db, order, quantity, user_id):
    line = order.lines[0]
    return apply_booking(
        db,
        order_id=order.id,
        order_line_id=line.id,
        sku_id=line.sku_id,
        quantity=quantity,
        cap_remaining=None,
        scanned_by=user_id,
        scan_image_path=None,
        confidence=None,
    )


class TestPickSettlesTheHold:
    def test_a_split_hold_is_picked_off_both_shelves(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """Three on the webshop shelf and one in the shop: both get emptied."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, store=1, webshop=3)
        assert _reserve(client).status_code == 200
        assert _order(client).status_code == 200
        order = db.query(Order).one()

        _pick(db, order, 4, owner_user.id)

        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_on_hand == 0
        assert _balance(db, sku.id, "store").quantity_on_hand == 0
        # And nothing is still set aside for an order that is packed.
        assert _balance(db, sku.id, "webshop").quantity_reserved == 0
        assert _balance(db, sku.id, "store").quantity_reserved == 0
        movements = {
            (m.inventory_location, m.quantity)
            for m in db.query(StockMovement).filter_by(sku_id=sku.id)
        }
        assert movements == {("webshop", -3), ("store", -1)}

    def test_the_webshop_shelf_empties_first(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """One bottle picked comes off the shelf the hold took first."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, store=5, webshop=5)
        _reserve(client, quantity=2)
        _order(client, quantity=2)
        order = db.query(Order).one()

        _pick(db, order, 1, owner_user.id)

        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_on_hand == 4
        assert _balance(db, sku.id, "store").quantity_on_hand == 5

    def test_stock_leaves_once_not_twice(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """The whole reason this exists: hold plus pick is one deduction."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, webshop=10)
        _reserve(client, quantity=4)
        _order(client, quantity=4)
        order = db.query(Order).one()

        _pick(db, order, 4, owner_user.id)

        db.expire_all()
        balance = _balance(db, sku.id, "webshop")
        assert (balance.quantity_on_hand, balance.quantity_reserved) == (6, 0)

    def test_the_hold_is_collected_once_it_is_empty(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=10)
        _reserve(client, quantity=2)
        _order(client, quantity=2)
        order = db.query(Order).one()

        _pick(db, order, 2, owner_user.id)

        db.expire_all()
        reservation = db.query(AdviceReservation).one()
        assert reservation.status == "collected"
        assert reservation.collected_at is not None

    def test_a_partly_picked_hold_stays_active(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=10)
        _reserve(client, quantity=3)
        _order(client, quantity=3)
        order = db.query(Order).one()

        _pick(db, order, 1, owner_user.id)

        db.expire_all()
        assert db.query(AdviceReservation).one().status == "active"

    def test_an_order_without_a_hold_uses_its_own_pool(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """The advice app may post an order it never reserved for."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, webshop=6)
        _order(client, quantity=2)
        order = db.query(Order).one()

        _pick(db, order, 2, owner_user.id)

        db.expire_all()
        assert _balance(db, sku.id, "webshop").quantity_on_hand == 4


class TestUndo:
    def test_undo_puts_the_bottle_back_on_its_own_shelf(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """Not on the order's pool: that could move a bottle across the shop."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, store=1, webshop=3)
        _reserve(client)
        _order(client)
        order = db.query(Order).one()
        _pick(db, order, 4, owner_user.id)

        for booking_id in sorted(
            b.id for b in db.query(Order).one().bookings
        ):
            undo_booking(db, booking_id=booking_id, performed_by=owner_user.id)

        db.expire_all()
        webshop = _balance(db, sku.id, "webshop")
        store = _balance(db, sku.id, "store")
        assert (webshop.quantity_on_hand, webshop.quantity_reserved) == (3, 3)
        assert (store.quantity_on_hand, store.quantity_reserved) == (1, 1)
        assert db.query(AdviceReservation).one().status == "active"


class TestTheAdviceAppMayNotSettleAPickedOrder:
    def test_collect_is_refused_after_a_pick(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """Settling here too would deduct the same wine twice."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=10)
        _reserve(client, quantity=2)
        _order(client, quantity=2)
        order = db.query(Order).one()
        _pick(db, order, 1, owner_user.id)

        resp = client.post(
            f"{RESERVATIONS_URL}/order_123/collect", headers=_headers()
        )

        assert resp.status_code == 409
        assert "gepickt" in resp.json()["detail"]

    def test_collect_still_works_for_an_untouched_hold(
        self, client, db, sample_org, monkeypatch
    ):
        """A shop pickup is still handed over at the counter, as it always was."""
        _configure(monkeypatch, sample_org.id)
        sku = _bottle(db, sample_org, store=6)
        client.post(
            RESERVATIONS_URL,
            json={
                "external_order_id": "afhaal_1",
                "fulfillment_method": "pickup",
                "inventory_location": "store",
                "lines": [{"source_product_id": "prd_a", "quantity": 2}],
            },
            headers=_headers(),
        )

        resp = client.post(
            f"{RESERVATIONS_URL}/afhaal_1/collect", headers=_headers()
        )

        assert resp.status_code == 200, resp.text
        db.expire_all()
        assert _balance(db, sku.id, "store").quantity_on_hand == 4

    def test_release_only_gives_back_what_is_still_held(
        self, client, db, sample_org, owner_user, monkeypatch
    ):
        """Bottles already in a parcel must not reappear on the shelf."""
        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        sku = _bottle(db, sample_org, webshop=10)
        _reserve(client, quantity=3)
        _order(client, quantity=3)
        order = db.query(Order).one()
        _pick(db, order, 1, owner_user.id)

        resp = client.post(
            f"{RESERVATIONS_URL}/order_123/release", headers=_headers()
        )

        assert resp.status_code == 200, resp.text
        db.expire_all()
        balance = _balance(db, sku.id, "webshop")
        # One bottle picked and gone; the other two released back to available.
        assert (balance.quantity_on_hand, balance.quantity_reserved) == (9, 0)


def test_the_hold_records_what_was_taken(
    client, db, sample_org, owner_user, monkeypatch
):
    """Consumed is tracked per shelf, so every later step can trust it."""
    _configure(monkeypatch, sample_org.id)
    _live(db, sample_org)
    _bottle(db, sample_org, store=1, webshop=3)
    _reserve(client)
    _order(client)
    order = db.query(Order).one()

    _pick(db, order, 3, owner_user.id)

    db.expire_all()
    rows = {
        line.inventory_location: (line.quantity, line.consumed_quantity)
        for line in db.query(AdviceReservationLine)
    }
    assert rows == {"webshop": (3, 3), "store": (1, 0)}


class TestTheSwitch:
    URL = "/api/channels/advice/mode"

    def test_a_platform_admin_flips_it(self, client, db, sample_org, admin_token, monkeypatch):
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)

        resp = client.post(
            self.URL, json={"mode": "live"}, headers=auth_header(admin_token)
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "live"

    def test_a_merchant_may_read_but_not_flip(
        self, client, db, sample_org, owner_token, monkeypatch
    ):
        """It decides whether their paid orders become warehouse work."""
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)

        read = client.get(
            "/api/channels/advice/status", headers=auth_header(owner_token)
        )
        write = client.post(
            self.URL, json={"mode": "live"}, headers=auth_header(owner_token)
        )

        assert read.status_code == 200, read.text
        assert read.json()["mode"] == "observe"
        assert write.status_code == 403

    def test_going_live_without_the_integration_is_refused(
        self, client, db, sample_org, admin_token, monkeypatch
    ):
        """A switch the advice app cannot reach would only ever look enabled."""
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)
        monkeypatch.setattr(settings, "advice_sales_api_key", "")

        resp = client.post(
            self.URL, json={"mode": "live"}, headers=auth_header(admin_token)
        )

        assert resp.status_code == 400
        assert "geconfigureerd" in resp.json()["detail"]

    def test_an_unknown_mode_is_refused(
        self, client, db, sample_org, admin_token, monkeypatch
    ):
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)

        resp = client.post(
            self.URL, json={"mode": "halverwege"}, headers=auth_header(admin_token)
        )

        assert resp.status_code == 400

    def test_going_back_to_observe_leaves_active_orders_alone(
        self, client, db, sample_org, admin_token, monkeypatch
    ):
        """Retracting live work would strand an order the courier is picking."""
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=6)
        _reserve(client, quantity=2)
        _order(client, quantity=2)

        resp = client.post(
            self.URL, json={"mode": "observe"}, headers=auth_header(admin_token)
        )

        assert resp.status_code == 200, resp.text
        assert db.query(Order).one().status == "active"


class TestTheMonthlyReport:
    URL = "/api/orders/reports/monthly-boxes"

    def test_a_picked_webshop_order_counts_apart(
        self, client, db, sample_org, owner_user, owner_token, monkeypatch
    ):
        """A parcel with a label is other work than a pallet for a restaurant."""
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=6)
        _reserve(client, quantity=2)
        _order(client, quantity=2)
        order = db.query(Order).one()
        _pick(db, order, 2, owner_user.id)
        db.expire_all()
        assert db.query(Order).one().status == "completed"

        resp = client.get(self.URL, headers=auth_header(owner_token))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["webshop"][0]["total_bottles"] == 2
        # And it is not counted a second time among the wholesale work.
        assert body["organizations"] == []

    def test_an_unpicked_webshop_order_counts_nowhere(
        self, client, db, sample_org, owner_token, monkeypatch
    ):
        """Nothing was done yet, so there is nothing to bill."""
        from tests.conftest import auth_header

        _configure(monkeypatch, sample_org.id)
        _live(db, sample_org)
        _bottle(db, sample_org, webshop=6)
        _reserve(client, quantity=2)
        _order(client, quantity=2)

        resp = client.get(self.URL, headers=auth_header(owner_token))

        assert resp.json()["webshop"] == []
