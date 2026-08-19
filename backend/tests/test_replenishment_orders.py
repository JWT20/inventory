"""A merchant ordering stock from the warehouse for their own shop or webshop.

The point of the whole flow: goods arrive as boxes, are sold as bottles, and a
pick is what turns one into the other. So the tests centre on the moment of
booking — the warehouse loses a box and the destination pool gains six bottles,
or neither happens.
"""

import pytest

from app.models import (
    Booking,
    InventoryBalance,
    Order,
    OrderLine,
    ReferenceImage,
    SKU,
    StockMovement,
)
from app.services.booking import apply_booking, undo_booking
from tests.conftest import auth_header


URL = "/api/orders/replenishment"


@pytest.fixture
def bottle(db, sample_org):
    sku = SKU(
        sku_code="FLES-REP",
        name="Bevoorradingswijn fles",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=True,
    )
    db.add(sku)
    db.commit()
    db.add(
        ReferenceImage(sku_id=sku.id, image_path="fles.jpg", processing_status="done")
    )
    db.commit()
    db.refresh(sku)
    return sku


@pytest.fixture
def box(db, sample_org, bottle):
    sku = SKU(
        sku_code="DOOS-REP",
        name="Bevoorradingswijn doos",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=False,
        bottle_sku_id=bottle.id,
    )
    db.add(sku)
    db.commit()
    # A vision product needs a reference photo before it can be matched by
    # camera; without one the order would park in pending_images.
    db.add(ReferenceImage(sku_id=sku.id, image_path="doos.jpg", processing_status="done"))
    db.commit()
    db.refresh(sku)
    return sku


def _stock(db, sample_org, sku, quantity, location="warehouse"):
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=sample_org.id,
            inventory_location=location,
            quantity_on_hand=quantity,
        )
    )
    db.commit()


def _balance(db, sku_id, location):
    return (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku_id, inventory_location=location)
        .one_or_none()
    )


def _book_one(db, order, line, sku_id, user_id, quantity=1):
    return apply_booking(
        db,
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku_id,
        quantity=quantity,
        cap_remaining=None,
        scanned_by=user_id,
        scan_image_path=None,
        confidence=None,
    )


class TestCreate:
    def test_creates_an_active_order_without_a_customer(
        self, client, db, owner_token, box
    ):
        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 2}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["order_kind"] == "replenishment"
        assert body["destination_location"] == "webshop"
        assert body["inventory_location"] == "warehouse"
        # No approval step: the merchant is asking for their own goods.
        assert body["status"] == "active"
        assert body["delivery_week"] is None
        assert body["lines"][0]["customer_id"] is None
        assert body["lines"][0]["klant"] == "Voorraad webshop"

    def test_same_product_twice_becomes_one_line(self, client, db, owner_token, box):
        resp = client.post(
            URL,
            json={
                "destination_location": "store",
                "lines": [
                    {"sku_id": box.id, "quantity": 2},
                    {"sku_id": box.id, "quantity": 3},
                ],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 201, resp.text
        assert len(resp.json()["lines"]) == 1
        assert resp.json()["lines"][0]["quantity"] == 5

    def test_unlinked_box_is_refused_up_front(self, client, db, owner_token, sample_org):
        loose = SKU(
            sku_code="DOOS-LOS",
            name="Ongekoppelde doos",
            organization_id=sample_org.id,
            product_type="vision",
        )
        db.add(loose)
        db.commit()

        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": loose.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 409
        assert "niet aan een fles gekoppeld" in resp.json()["detail"]
        assert db.query(Order).count() == 0

    def test_bottle_needs_no_link(self, client, db, owner_token, bottle):
        resp = client.post(
            URL,
            json={
                "destination_location": "store",
                "lines": [{"sku_id": bottle.id, "quantity": 4}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 201, resp.text

    def test_warehouse_is_not_a_destination(self, client, owner_token, box):
        resp = client.post(
            URL,
            json={
                "destination_location": "warehouse",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 422

    def test_another_merchants_product_is_not_found(
        self, client, db, owner_token, sample_org
    ):
        from app.models import Organization

        other = Organization(name="Andere handelaar", slug="andere-handelaar-rep")
        db.add(other)
        db.commit()
        foreign = SKU(
            sku_code="FLES-VREEMD",
            name="Fles elders",
            organization_id=other.id,
            product_type="vision",
            is_bottle=True,
        )
        db.add(foreign)
        db.commit()

        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": foreign.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 404

    def test_customers_may_not_replenish(self, client, customer_token, box):
        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(customer_token),
        )

        assert resp.status_code == 403

    def test_couriers_may_not_replenish(self, client, courier_token, box):
        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 403

    def test_order_waits_for_a_reference_photo(
        self, client, db, owner_token, sample_org, bottle
    ):
        """A vision product the camera cannot match yet is not pickable."""
        photoless = SKU(
            sku_code="DOOS-GEENFOTO",
            name="Doos zonder foto",
            organization_id=sample_org.id,
            product_type="vision",
            bottle_sku_id=bottle.id,
        )
        db.add(photoless)
        db.commit()

        resp = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": photoless.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "pending_images"


class TestPickingConvertsBoxesToBottles:
    def test_one_picked_box_becomes_six_bottles(
        self, client, db, owner_token, owner_user, box, bottle, sample_org
    ):
        _stock(db, sample_org, box, 3)
        created = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 2}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        line = order.lines[0]

        _book_one(db, order, line, box.id, owner_user.id)

        db.expire_all()
        assert _balance(db, box.id, "warehouse").quantity_on_hand == 2
        assert _balance(db, bottle.id, "webshop").quantity_on_hand == 6
        # The box never appears in a pool that only holds bottles.
        assert _balance(db, box.id, "webshop") is None

    def test_a_picked_bottle_stays_one_bottle(
        self, client, db, owner_token, owner_user, bottle, sample_org
    ):
        _stock(db, sample_org, bottle, 10)
        created = client.post(
            URL,
            json={
                "destination_location": "store",
                "lines": [{"sku_id": bottle.id, "quantity": 4}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        line = order.lines[0]

        _book_one(db, order, line, bottle.id, owner_user.id, quantity=4)

        db.expire_all()
        assert _balance(db, bottle.id, "warehouse").quantity_on_hand == 6
        assert _balance(db, bottle.id, "store").quantity_on_hand == 4

    def test_both_pools_move_or_neither_does(
        self, client, db, owner_token, owner_user, box, bottle, sample_org
    ):
        """A short warehouse balance must not leave bottles on the shelf."""
        _stock(db, sample_org, box, 0)
        created = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        line = order.lines[0]

        with pytest.raises(Exception):
            _book_one(db, order, line, box.id, owner_user.id)

        db.rollback()
        db.expire_all()
        assert _balance(db, box.id, "warehouse").quantity_on_hand == 0
        assert _balance(db, bottle.id, "webshop") is None
        assert db.query(Booking).count() == 0

    def test_unlinking_the_bottle_after_ordering_blocks_the_pick(
        self, client, db, owner_token, owner_user, box, sample_org
    ):
        _stock(db, sample_org, box, 3)
        created = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        line = order.lines[0]

        box.bottle_sku_id = None
        db.commit()

        with pytest.raises(Exception):
            _book_one(db, order, line, box.id, owner_user.id)

        db.rollback()
        db.expire_all()
        assert _balance(db, box.id, "warehouse").quantity_on_hand == 3

    def test_undo_takes_the_bottles_back_out(
        self, client, db, owner_token, owner_user, box, bottle, sample_org
    ):
        _stock(db, sample_org, box, 3)
        created = client.post(
            URL,
            json={
                "destination_location": "store",
                "lines": [{"sku_id": box.id, "quantity": 2}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        line = order.lines[0]
        result = _book_one(db, order, line, box.id, owner_user.id)

        undo_booking(db, booking_id=result.last_booking_id, performed_by=owner_user.id)

        db.expire_all()
        assert _balance(db, box.id, "warehouse").quantity_on_hand == 3
        assert _balance(db, bottle.id, "store").quantity_on_hand == 0

    def test_a_customer_order_credits_nothing(
        self, db, owner_user, box, bottle, sample_org
    ):
        """The conversion is what replenishment adds; a normal pick is unchanged."""
        _stock(db, sample_org, box, 5)
        order = Order(
            organization_id=sample_org.id,
            reference="ORD-NORMAAL",
            status="active",
            channel="manual",
        )
        db.add(order)
        db.flush()
        line = OrderLine(order_id=order.id, sku_id=box.id, klant="Klant", quantity=1)
        db.add(line)
        db.commit()

        _book_one(db, order, line, box.id, owner_user.id)

        db.expire_all()
        assert _balance(db, box.id, "warehouse").quantity_on_hand == 4
        assert _balance(db, bottle.id, "store") is None
        assert _balance(db, bottle.id, "webshop") is None

    def test_the_credit_is_logged_as_a_movement(
        self, client, db, owner_token, owner_user, box, bottle, sample_org
    ):
        _stock(db, sample_org, box, 3)
        created = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        _book_one(db, order, order.lines[0], box.id, owner_user.id)

        credit = (
            db.query(StockMovement)
            .filter_by(sku_id=bottle.id, inventory_location="webshop")
            .one()
        )
        assert credit.quantity == 6
        assert credit.movement_type == "transfer"
        assert order.reference in credit.note


class TestReportsIgnoreReplenishment:
    def test_weekly_summary_skips_replenishment(
        self, client, db, owner_token, box, sample_org
    ):
        client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 2}],
            },
            headers=auth_header(owner_token),
        )

        resp = client.get(
            "/api/orders/weekly-summary",
            params={"week": "2026-W34"},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["grand_total_quantity"] == 0

    def test_monthly_report_skips_replenishment(
        self, client, db, owner_token, owner_user, box, sample_org
    ):
        _stock(db, sample_org, box, 3)
        created = client.post(
            URL,
            json={
                "destination_location": "webshop",
                "lines": [{"sku_id": box.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        ).json()
        order = db.get(Order, created["id"])
        _book_one(db, order, order.lines[0], box.id, owner_user.id)
        db.expire_all()
        assert db.get(Order, created["id"]).status == "completed"

        resp = client.get(
            "/api/orders/reports/monthly-boxes", headers=auth_header(owner_token)
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["organizations"] == []
