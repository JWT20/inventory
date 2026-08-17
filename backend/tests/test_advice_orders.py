"""Delivery orders from the advice app, taken in to observe."""

from app.config import settings
from app.models import (
    ChannelConnection,
    ChannelSyncLog,
    InventoryBalance,
    Order,
    OrderDeliveryAddress,
    SKU,
)


API_KEY = "test-advice-write-key"
BASE_URL = "/api/integrations/advice/orders"


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
            inventory_location="warehouse",
            quantity_on_hand=on_hand,
        )
    )
    db.commit()
    return sku


ADDRESS = {
    "recipient_name": "Anna de Vries",
    "street": "Turfsingel",
    "house_number": "8",
    "house_number_suffix": "B",
    "postal_code": "9712 KR",
    "city": "Groningen",
    "country": "nl",
    "phone": "0612345678",
}


def _payload(**overrides) -> dict:
    payload = {
        "external_order_id": "order_123",
        "order_reference": "JUR-2026-8CERZC",
        "fulfillment_method": "dockscan",
        "inventory_location": "warehouse",
        "ordered_at": "2026-08-17T13:35:00",
        "delivery_address": dict(ADDRESS),
        "lines": [{"source_product_id": "prd_a", "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def test_a_delivery_order_lands_as_an_observed_order(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200
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
    assert order.inventory_location == "warehouse"
    # No delivery week: this order belongs to no week's planning.
    assert order.delivery_week is None
    assert [(line.sku_id, line.quantity, line.klant) for line in order.lines] == [
        (sku.id, 2, "Anna de Vries")
    ]
    # A channel order has no Dockscan customer row to point at.
    assert order.lines[0].customer_id is None


def test_observing_never_touches_stock(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    client.post(BASE_URL, json=_payload(), headers=_headers())

    db.expire_all()
    balance = (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku.id, inventory_location="warehouse")
        .one()
    )
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)


def test_the_address_is_stored_normalized(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    address = db.query(OrderDeliveryAddress).one()
    assert address.order_id == response.json()["order_id"]
    assert address.recipient_name == "Anna de Vries"
    assert address.house_number == "8"
    assert address.house_number_suffix == "B"
    assert address.postal_code == "9712 KR"
    assert address.city == "Groningen"
    # ISO country codes are stored upper case whatever the caller sent.
    assert address.country == "NL"
    assert address.phone == "0612345678"


def test_a_retry_updates_the_order_while_it_is_still_observed(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    _bottle(db, sample_org, "prd_b")

    first = client.post(BASE_URL, json=_payload(), headers=_headers())
    moved = dict(ADDRESS, street="Nieuweweg", house_number="12", house_number_suffix=None)
    retry = client.post(
        BASE_URL,
        json=_payload(
            delivery_address=moved,
            lines=[{"source_product_id": "prd_b", "quantity": 1}],
        ),
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
    assert (order.delivery_address.street, order.delivery_address.house_number) == (
        "Nieuweweg",
        "12",
    )
    assert order.delivery_address.house_number_suffix is None


def test_a_retry_leaves_an_order_someone_is_working_on_alone(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    _bottle(db, sample_org, "prd_b")

    first = client.post(BASE_URL, json=_payload(), headers=_headers())
    order = db.get(Order, first.json()["order_id"])
    order.status = "active"
    db.commit()

    retry = client.post(
        BASE_URL,
        json=_payload(lines=[{"source_product_id": "prd_b", "quantity": 5}]),
        headers=_headers(),
    )

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    # Reported as matched, because it is — but not written over live work.
    assert retry.json()["matched"][0]["quantity"] == 5
    db.expire_all()
    order = db.get(Order, first.json()["order_id"])
    assert [(line.sku.source_product_id, line.quantity) for line in order.lines] == [
        ("prd_a", 2)
    ]


def test_repeated_products_collapse_into_one_line(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    response = client.post(
        BASE_URL,
        json=_payload(
            lines=[
                {"source_product_id": "prd_a", "quantity": 2},
                {"source_product_id": "prd_a", "quantity": 1},
            ]
        ),
        headers=_headers(),
    )

    assert response.json()["matched"] == [
        {"source_product_id": "prd_a", "sku_code": sku.sku_code, "quantity": 3}
    ]
    order = db.get(Order, response.json()["order_id"])
    assert [(line.sku_id, line.quantity) for line in order.lines] == [(sku.id, 3)]


def test_an_unlinked_product_reports_itself_without_losing_the_order(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    response = client.post(
        BASE_URL,
        json=_payload(
            lines=[
                {"source_product_id": "prd_a", "quantity": 2},
                {"source_product_id": "prd_unknown", "quantity": 1},
            ]
        ),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["unmatched"] == ["prd_unknown"]
    assert [line["sku_code"] for line in body["matched"]] == [sku.sku_code]
    order = db.get(Order, body["order_id"])
    assert len(order.lines) == 1


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


def test_the_first_order_creates_an_observing_connection(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    client.post(BASE_URL, json=_payload(), headers=_headers())

    connection = db.query(ChannelConnection).one()
    assert (connection.channel, connection.mode) == ("advice", "observe")
    assert connection.last_synced_at is not None


def test_a_live_connection_refuses_the_import(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")
    db.add(
        ChannelConnection(
            organization_id=sample_org.id, channel="advice", mode="live"
        )
    )
    db.commit()

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 409
    assert "live" in response.json()["detail"]
    assert db.query(Order).count() == 0


def test_a_pickup_order_is_not_accepted_here(client, db, sample_org, monkeypatch):
    """Pickups reserve store stock through /reservations and stay out of orders."""
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    response = client.post(
        BASE_URL,
        json=_payload(fulfillment_method="pickup", inventory_location="store"),
        headers=_headers(),
    )

    assert response.status_code == 422
    assert db.query(Order).count() == 0


def test_an_order_without_an_address_is_refused(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    payload = _payload()
    del payload["delivery_address"]
    response = client.post(BASE_URL, json=payload, headers=_headers())

    assert response.status_code == 422


def test_the_wrong_key_is_refused(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, "prd_a")

    response = client.post(
        BASE_URL, json=_payload(), headers={"Authorization": "Bearer nope"}
    )

    assert response.status_code == 401
    assert db.query(Order).count() == 0
