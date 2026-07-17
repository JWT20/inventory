"""Synchronous Shopify fulfillment write-back for the shipping-label gate.

Unlike inventory mirroring, this operation is not best-effort: Scan & Boek may
only show an order as shipped after Shopify accepted the fulfillment. Keeping it
inside the request also gives the courier an actionable retry error.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ChannelConnection, Order
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_access_token,
    has_access_token,
)
from app.services.shopify import ShopifyClient


class ShopifyFulfillmentError(RuntimeError):
    """A safe, operator-facing Shopify fulfillment failure."""


REQUIRED_FULFILLMENT_SCOPES = {
    "write_merchant_managed_fulfillment_orders",
    "write_third_party_fulfillment_orders",
}


def fulfill_shopify_order(
    db: Session,
    order: Order,
    *,
    tracking_info: dict[str, str] | None = None,
    client_factory=ShopifyClient,
) -> bool:
    """Create Shopify fulfillments for a fully picked channel order.

    Returns whether Shopify created at least one fulfillment. An already
    fulfilled Shopify order is an idempotent success. No commit is performed;
    the label route owns the local transaction.
    """
    if order.channel != "shopify" or not order.external_id:
        raise ShopifyFulfillmentError("Order mist een geldige Shopify-koppeling")
    if order.organization_id is None:
        raise ShopifyFulfillmentError("Order mist een organisatie")

    connection = (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == order.organization_id,
            ChannelConnection.channel == "shopify",
            ChannelConnection.mode == "live",
            ChannelConnection.status == "active",
        )
        .first()
    )
    if not connection or not connection.shop_domain or not has_access_token(connection):
        raise ShopifyFulfillmentError("Shopify is niet actief gekoppeld")

    granted_scopes = {
        scope.strip() for scope in (connection.scope or "").split(",") if scope.strip()
    }
    missing_scopes = REQUIRED_FULFILLMENT_SCOPES - granted_scopes
    if missing_scopes:
        raise ShopifyFulfillmentError(
            "Shopify-koppeling mist verzendrechten; verbind Shopify opnieuw"
        )

    try:
        access_token = get_access_token(connection)
    except CredentialEncryptionError as exc:
        raise ShopifyFulfillmentError(
            "Shopify-credential kan niet veilig worden ontsleuteld"
        ) from exc
    client = client_factory(
        shop_domain=connection.shop_domain,
        access_token=access_token,
    )
    try:
        created = client.fulfill_order(
            order.external_id,
            notify_customer=False,
            tracking_info=tracking_info,
        )
    except Exception as exc:
        raise ShopifyFulfillmentError(
            "Shopify kon de order niet als verzonden markeren; probeer opnieuw"
        ) from exc

    order.channel_fulfillment_status = "fulfilled"
    return created
