"""Pickup orders from the advice app, collected off the webshop shelf.

Unlike a delivery order (test_advice_orders.py) this never becomes a parcel:
there is no address, and it must never reach Veloyd. It still needs a pick
task, which is exactly what a plain counter pickup (settled through
/reservations) never gets.
"""

import app.routers.integrations as integrations
from app.config import settings
from app.models import (
    ChannelConnection,
    ChannelSyncLog,
    InventoryBalance,
    Order,
    OrderDeliveryAddress,
    OrderParcel,
    ReferenceImage,
    SKU,
)


API_KEY = "test-advice-write-key"
BASE_URL = "/api/integrations/advice/pickup-orders"


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_sales_api_key", API_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def _bottle(db, org, product_id: str, on_hand: int = 8) -> SKU:
    sku = SKU(
        sku_code=f"{product_id.upper()}-FLES",
        name=product_id,
        organization_id=org.id,
        is_bottle=True,
        source_product_id=product_id,
    )
    db.add(sku)
    db.flush()
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=org.id,
            inventory_location="webshop",
            quantity_on_hand=on_hand,
        )
    )
    db.commit()
    return sku


def _payload(**overrides) -> dict:
    payload = {
        "external_order_id": "order_123",
        "order_reference": "JUR-2026-8CERZC",
        "customer_name": "Anna de Vries",
        "ordered_at": "2026-08-17T13:35:00",
        "lines": [{"source_product_id": "prd_a", "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def _go_live(db, org) -> None:
    db.add(ChannelConnection(organization_id=org.id, channel="advice", mode="live"))
    db.commit()


def test_a_pickup_order_lands_as_an_observed_order(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "observed"
    assert body["duplicate"] is False
    assert body["reference"].startswith("ADV-")
    assert body["matched"] == [
        {"source_product_id": "prd_a", "sku_code": sku.sku_code, "quantity": 2}
    ]
    assert body["unmatched"] == []

    order = db.get(Order, body["order_id"])
    assert (order.channel, order.status) == ("advice", "observed")
    assert order.external_id == "order_123"
    assert order.channel_reference == "JUR-2026-8CERZC"
    # Booked off the webshop shelf, same pool a delivery packs from — this is
    # where a non-counter pickup physically sits.
    assert order.inventory_location == "webshop"
    assert order.delivery_week is None
    # Shown in the merchant order notes, so a picker never mistakes it for a
    # delivery.
    assert "niet verzenden" in order.remarks.lower()
    assert [(line.sku_id, line.quantity, line.klant) for line in order.lines] == [
        (sku.id, 2, "Anna de Vries")
    ]
    # A channel order has no Dockscan customer row to point at.
    assert order.lines[0].customer_id is None


def test_no_delivery_address_is_ever_stored(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    client.post(BASE_URL, json=_payload(), headers=_headers())

    assert db.query(OrderDeliveryAddress).count() == 0


def test_a_missing_customer_name_falls_back_to_a_stand_in_label(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    payload = _payload()
    del payload["customer_name"]
    response = client.post(BASE_URL, json=payload, headers=_headers())

    order = db.get(Order, response.json()["order_id"])
    assert order.lines[0].klant == "Afhalen Stavangerweg"


def test_observing_never_touches_stock(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    client.post(BASE_URL, json=_payload(), headers=_headers())

    db.expire_all()
    balance = (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku.id, inventory_location="webshop")
        .one()
    )
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)


def test_a_live_connection_makes_the_order_pickable(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")
    db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
    _go_live(db, sample_org)

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200, response.text
    assert db.query(Order).one().status == "active"


def test_a_live_order_without_a_photo_waits_for_one(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    _go_live(db, sample_org)

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200, response.text
    assert db.query(Order).one().status == "pending_images"


def test_a_live_order_with_an_unknown_product_is_parked(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")
    db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
    _go_live(db, sample_org)

    response = client.post(
        BASE_URL,
        json=_payload(
            lines=[
                {"source_product_id": "prd_a", "quantity": 1},
                {"source_product_id": "prd_onbekend", "quantity": 1},
            ]
        ),
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert db.query(Order).one().status == "pending_product"


def test_an_active_pickup_order_never_becomes_a_parcel(
    client, db, sample_org, monkeypatch
):
    """The one thing this endpoint exists to avoid."""
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")
    db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
    _go_live(db, sample_org)

    def _fail_if_called(db_arg, order_arg):
        raise AssertionError(
            "create_parcels_best_effort must never run for a pickup order"
        )

    monkeypatch.setattr(integrations, "create_parcels_best_effort", _fail_if_called)

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200, response.text
    assert db.query(Order).one().status == "active"
    assert db.query(OrderParcel).count() == 0


def test_a_retry_updates_the_order_while_it_is_still_observed(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    _bottle(db, sample_org, "prd_b")

    first = client.post(BASE_URL, json=_payload(), headers=_headers())
    retry = client.post(
        BASE_URL,
        json=_payload(lines=[{"source_product_id": "prd_b", "quantity": 1}]),
        headers=_headers(),
    )

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["order_id"] == first.json()["order_id"]
    assert db.query(Order).count() == 1

    db.expire_all()
    order = db.get(Order, first.json()["order_id"])
    assert [(line.sku.source_product_id, line.quantity) for line in order.lines] == [
        ("prd_b", 1)
    ]


def test_the_reconciliation_view_gets_one_log_row_per_order(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    client.post(BASE_URL, json=_payload(), headers=_headers())
    client.post(
        BASE_URL,
        json=_payload(lines=[{"source_product_id": "prd_unknown", "quantity": 1}]),
        headers=_headers(),
    )

    log = db.query(ChannelSyncLog).one()
    assert (log.channel, log.external_id) == ("advice", "order_123")
    assert log.action == "updated"
    assert log.matched_lines == 0
    assert log.unmatched_eans == '["prd_unknown"]'


def test_a_delivery_and_a_pickup_order_can_share_one_channel_connection(
    client, db, sample_org, monkeypatch
):
    """Both are advice-app orders; one live/observe switch governs both."""
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    _bottle(db, sample_org, "prd_b")

    client.post(
        "/api/integrations/advice/orders",
        json={
            "external_order_id": "delivery_1",
            "fulfillment_method": "dockscan",
            "inventory_location": "webshop",
            "delivery_address": {
                "recipient_name": "Anna de Vries",
                "street": "Turfsingel",
                "house_number": "8",
                "postal_code": "9712 KR",
                "city": "Groningen",
            },
            "lines": [{"source_product_id": "prd_a", "quantity": 1}],
        },
        headers=_headers(),
    )
    client.post(
        BASE_URL,
        json=_payload(external_order_id="pickup_1"),
        headers=_headers(),
    )

    assert db.query(ChannelConnection).count() == 1
    assert db.query(Order).count() == 2


def test_the_wrong_key_is_refused(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    response = client.post(
        BASE_URL, json=_payload(), headers={"Authorization": "Bearer nope"}
    )

    assert response.status_code == 401
    assert db.query(Order).count() == 0
