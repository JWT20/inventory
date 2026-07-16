import pytest

from app.models import ChannelConnection, Order, Organization
from app.services.fulfillment_sync import (
    REQUIRED_FULFILLMENT_SCOPES,
    ShopifyFulfillmentError,
    fulfill_shopify_order,
)
from tests.conftest import store_test_channel_token


def _order_and_connection(db, *, scopes=REQUIRED_FULFILLMENT_SCOPES):
    org = Organization(name="Sokken", slug="sokken")
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.flush()
    order = Order(
        organization_id=org.id,
        reference="SHO-TESTFUL",
        channel="shopify",
        external_id="13105242374489",
        channel_reference="1272",
        channel_fulfillment_status="unfulfilled",
        status="completed",
    )
    connection = ChannelConnection(
        organization_id=org.id,
        channel="shopify",
        shop_domain="x.myshopify.com",
        scope=",".join(sorted(scopes)),
        mode="live",
        status="active",
    )
    db.add_all([order, connection])
    db.flush()
    store_test_channel_token(db, connection)
    db.commit()
    db.refresh(order)
    return order


def test_fulfill_shopify_order_updates_external_status(db):
    order = _order_and_connection(db)
    seen = {}

    class FakeClient:
        def __init__(self, shop_domain, access_token):
            seen["credentials"] = (shop_domain, access_token)

        def fulfill_order(
            self, external_id, notify_customer=False, tracking_info=None
        ):
            seen["call"] = (external_id, notify_customer, tracking_info)
            return True

    tracking_info = {"number": "V793AUDS9F4MB"}
    assert fulfill_shopify_order(
        db, order, tracking_info=tracking_info, client_factory=FakeClient
    ) is True
    assert order.channel_fulfillment_status == "fulfilled"
    assert seen["credentials"] == ("x.myshopify.com", "tok")
    assert seen["call"] == ("13105242374489", False, tracking_info)


def test_fulfill_shopify_order_requires_reconnect_for_old_token(db):
    order = _order_and_connection(db, scopes={"read_orders", "read_products"})

    with pytest.raises(ShopifyFulfillmentError, match="verbind Shopify opnieuw"):
        fulfill_shopify_order(db, order)

    assert order.channel_fulfillment_status == "unfulfilled"
