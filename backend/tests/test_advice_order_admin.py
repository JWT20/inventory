"""The merchant's view on advice-app delivery orders."""

import datetime

from app.config import settings
from app.models import (
    ChannelSyncLog,
    Order,
    OrderDeliveryAddress,
    OrderLine,
    SKU,
)
from tests.conftest import auth_header


BASE_URL = "/api/advice-orders"


def _bottle(db, org, code: str) -> SKU:
    sku = SKU(
        sku_code=code,
        name=f"Wijn {code}",
        organization_id=org.id,
        is_bottle=True,
        source_product_id=f"prd_{code.lower()}",
    )
    db.add(sku)
    db.commit()
    return sku


def _order(
    db,
    org,
    sku,
    *,
    external_id: str,
    status: str = "observed",
    quantity: int = 2,
    with_address: bool = True,
) -> Order:
    order = Order(
        organization_id=org.id,
        channel="advice",
        external_id=external_id,
        reference=f"ADV-{external_id.upper()}",
        channel_reference=f"JUR-2026-{external_id}",
        status=status,
        inventory_location="warehouse",
        ordered_at=datetime.datetime(2026, 8, 17, 13, 35),
    )
    db.add(order)
    db.flush()
    db.add(
        OrderLine(
            order_id=order.id,
            sku_id=sku.id,
            quantity=quantity,
            klant="Anna de Vries",
        )
    )
    if with_address:
        db.add(
            OrderDeliveryAddress(
                order_id=order.id,
                recipient_name="Anna de Vries",
                street="Turfsingel",
                house_number="8",
                house_number_suffix="B",
                postal_code="9712 KR",
                city="Groningen",
                country="NL",
                phone="0612345678",
            )
        )
    db.commit()
    db.refresh(order)
    return order


def test_merchant_sees_the_order_with_the_address_it_ships_to(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="order_a")

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["order_reference"] == "JUR-2026-order_a"
    assert row["reference"] == "ADV-ORDER_A"
    assert row["status"] == "observed"
    assert row["total_quantity"] == 2
    assert row["lines"][0]["sku_code"] == "DELIV-A"
    assert row["delivery_address"] == {
        "recipient_name": "Anna de Vries",
        "street": "Turfsingel",
        "house_number": "8",
        "house_number_suffix": "B",
        "postal_code": "9712 KR",
        "city": "Groningen",
        "country": "NL",
        "phone": "0612345678",
        "email": None,
    }


def test_observed_orders_are_the_default(client, db, owner_token, sample_org):
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="observed_one")
    _order(db, sample_org, sku, external_id="promoted", status="active")

    default = client.get(BASE_URL, headers=auth_header(owner_token))
    everything = client.get(f"{BASE_URL}?status=all", headers=auth_header(owner_token))

    assert [row["external_order_id"] for row in default.json()] == ["observed_one"]
    assert {row["external_order_id"] for row in everything.json()} == {
        "observed_one",
        "promoted",
    }


def test_an_unknown_status_is_refused(client, db, owner_token, sample_org):
    resp = client.get(f"{BASE_URL}?status=verzonden", headers=auth_header(owner_token))

    assert resp.status_code == 400


def test_unlinked_products_are_named_so_the_order_cannot_ship_short(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="order_a")
    db.add(
        ChannelSyncLog(
            organization_id=sample_org.id,
            channel="advice",
            external_id="order_a",
            action="created",
            matched_lines=1,
            unmatched_eans='["prd_onbekend"]',
        )
    )
    db.commit()

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert resp.json()[0]["unmatched_products"] == ["prd_onbekend"]


def test_a_malformed_sync_log_does_not_hide_the_order(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="order_a")
    db.add(
        ChannelSyncLog(
            organization_id=sample_org.id,
            channel="advice",
            external_id="order_a",
            action="created",
            matched_lines=1,
            unmatched_eans="niet-json",
        )
    )
    db.commit()

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert resp.status_code == 200
    assert resp.json()[0]["unmatched_products"] == []


def test_pickup_orders_and_other_channels_stay_out(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="advice_one")
    shopify = Order(
        organization_id=sample_org.id,
        channel="shopify",
        external_id="shop_1",
        reference="SHO-1",
        status="observed",
    )
    db.add(shopify)
    db.commit()

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert [row["external_order_id"] for row in resp.json()] == ["advice_one"]


def test_admin_defaults_to_the_configured_advice_org(
    client, db, admin_token, sample_org, monkeypatch
):
    sample_org.modules = ["inventory", "orders"]
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="configured")
    monkeypatch.setattr(settings, "advice_stock_organization_id", sample_org.id)

    resp = client.get(BASE_URL, headers=auth_header(admin_token))

    assert resp.status_code == 200
    assert [row["external_order_id"] for row in resp.json()] == ["configured"]


def test_an_order_without_an_address_still_lists(
    client, db, owner_token, sample_org
):
    """A legacy or hand-made advice order must not break the view."""
    sku = _bottle(db, sample_org, "DELIV-A")
    _order(db, sample_org, sku, external_id="order_a", with_address=False)

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert resp.status_code == 200
    assert resp.json()[0]["delivery_address"] is None
