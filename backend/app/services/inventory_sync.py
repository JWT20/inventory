"""Sales-channel inventory write-back — the app is the source of truth.

Every local stock change pushes the *absolute* available quantity to all live
sales channels. Absolute (not delta) makes the operation idempotent and
self-healing: a missed push never drifts because the next one overwrites with
the truth.

Design:
- Only orgs with a **live** Shopify connection are touched (observe-mode never
  acts). Vision/wine products have no EAN and are skipped for free.
- The EAN is the join key: it equals the Shopify variant barcode. The resolved
  ``inventory_item_id`` is cached on the SKU and the primary ``location_id`` on
  the connection, so steady-state pushes are a single mutation.
- **Best-effort**: this is meant to run in a FastAPI ``BackgroundTask`` after the
  response. Every failure is swallowed + logged — a Shopify outage must never
  break a pick. The local books are already committed by the caller.
"""
from __future__ import annotations

import logging

from app.database import SessionLocal
from app.models import ChannelConnection, InventoryBalance, SKU
from app.services.bol import BolAPIError, BolClient
from app.services.channel_credentials import get_access_token
from app.services.shopify import ShopifyClient

logger = logging.getLogger(__name__)


def _live_shopify_connection(db, organization_id: int) -> ChannelConnection | None:
    return (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == organization_id,
            ChannelConnection.channel == "shopify",
            ChannelConnection.mode == "live",
            ChannelConnection.status == "active",
        )
        .first()
    )


def _live_bol_connection(db, organization_id: int) -> ChannelConnection | None:
    return (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == organization_id,
            ChannelConnection.channel == "bol",
            ChannelConnection.mode == "live",
            ChannelConnection.status == "active",
        )
        .first()
    )


def _available(db, sku_id: int, organization_id: int) -> int:
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .first()
    )
    return balance.quantity_available if balance else 0


def push_available(db, sku_id: int, organization_id: int, *, client_factory=ShopifyClient) -> bool:
    """Core write-back on a supplied session (no commit, no error-swallowing).

    Returns True if a value was pushed to Shopify, False for the no-op cases
    (vision product, no live connection, unknown variant). Caches the resolved
    location_id / inventory_item_id on the session for the caller to commit.
    """
    sku = db.get(SKU, sku_id)
    if not sku or not sku.ean:
        return False  # vision/wine product — nothing to mirror

    conn = _live_shopify_connection(db, organization_id)
    if not conn:
        return False

    client = client_factory(
        shop_domain=conn.shop_domain,
        access_token=get_access_token(conn),
    )
    if not client.configured:
        return False

    # Resolve + cache the shop's primary location.
    location_id = conn.shopify_location_id
    if not location_id:
        location_id = client.primary_location_id()
        if not location_id:
            logger.warning("Shopify push: no active location for org %s", organization_id)
            return False
        conn.shopify_location_id = location_id

    # Resolve + cache this SKU's inventory_item_id via its barcode/EAN.
    inventory_item_id = sku.shopify_inventory_item_id
    if not inventory_item_id:
        inventory_item_id = client.resolve_inventory_item_id(sku.ean)
        if not inventory_item_id:
            logger.info(
                "Shopify push: no variant with barcode %s (sku %s)", sku.ean, sku_id
            )
            return False
        sku.shopify_inventory_item_id = inventory_item_id

    available = _available(db, sku_id, organization_id)
    client.set_inventory_available(inventory_item_id, location_id, available)
    return True


def push_inventory_to_shopify(sku_id: int, organization_id: int) -> None:
    """Push one SKU's available stock to Shopify. Safe to schedule blindly.

    Opens its own session (it runs after the request session is closed) and never
    raises — failures are logged. No-ops for vision products, orgs without a live
    Shopify connection, or variants Shopify does not know.
    """
    db = SessionLocal()
    try:
        push_available(db, sku_id, organization_id)
        db.commit()  # persist any freshly-cached ids
    except Exception:  # best-effort: never break the caller's flow
        logger.exception(
            "Shopify inventory push failed (sku %s, org %s)", sku_id, organization_id
        )
        db.rollback()
    finally:
        db.close()


def push_bol_available(
    db, sku_id: int, organization_id: int, *, client_factory=BolClient
) -> bool:
    """Push one local available quantity to its single matching FBR offer.

    Missing offers are a safe no-op. Multiple FBR offers for one EAN are
    rejected: copying one warehouse balance into multiple independently
    sellable offers would create oversell risk.
    """
    sku = db.get(SKU, sku_id)
    if not sku or not sku.ean:
        return False
    if not _live_bol_connection(db, organization_id):
        return False

    client = client_factory()
    if not client.configured:
        return False
    def resolve_offer_id() -> str | None:
        offer_ids = client.find_fbr_offer_ids(sku.ean)
        if not offer_ids:
            logger.info(
                "bol push: no FBR offer with EAN %s (sku %s)", sku.ean, sku_id
            )
            return None
        if len(offer_ids) > 1:
            raise BolAPIError(
                f"EAN {sku.ean} heeft meerdere FBR-offers; voorraad niet aangepast"
            )
        sku.bol_offer_id = offer_ids[0]
        return offer_ids[0]

    had_cached_offer = bool(sku.bol_offer_id)
    offer_id = sku.bol_offer_id or resolve_offer_id()
    if not offer_id:
        return False
    available = _available(db, sku_id, organization_id)
    try:
        client.set_offer_stock(offer_id, available)
    except BolAPIError as exc:
        if not had_cached_offer or exc.status_code != 404:
            raise
        # The offer may have been deleted/recreated in bol. Invalidate once and
        # resolve from the stable EAN; any second failure is returned normally.
        logger.info(
            "bol push: cached offer %s is stale for sku %s; resolving again",
            offer_id,
            sku_id,
        )
        sku.bol_offer_id = None
        offer_id = resolve_offer_id()
        if not offer_id:
            return False
        client.set_offer_stock(offer_id, available)
    return True


def push_inventory_to_bol(sku_id: int, organization_id: int) -> None:
    """Best-effort bol write-back for one SKU in its own database session."""
    db = SessionLocal()
    try:
        push_bol_available(db, sku_id, organization_id)
        db.commit()
    except Exception:
        logger.exception(
            "bol inventory push failed (sku %s, org %s)", sku_id, organization_id
        )
        db.rollback()
    finally:
        db.close()


def push_inventory_to_channels(sku_id: int, organization_id: int) -> None:
    """Mirror one SKU to every live channel without coupling their failures."""
    push_inventory_to_shopify(sku_id, organization_id)
    push_inventory_to_bol(sku_id, organization_id)
