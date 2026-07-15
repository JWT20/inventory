"""Shopify adapter — translates Shopify Admin API orders into the internal
``NormalizedChannelOrder`` shape and pulls them incrementally.

Deliberately thin and isolated: the channel-agnostic importer
(``services.channel_import``) does all the order/SKU work, so this module only
knows Shopify's JSON shape. The mapping (:func:`to_normalized`) is a pure
function so it can be unit-tested with canned payloads — the live HTTP call is a
small wrapper around it.

EAN source: the variant ``barcode`` field (where Shopify stores EAN/GTIN). A
line without a barcode is reported as unmatched by the importer, which is exactly
what observe-mode is meant to surface.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChannelConnection
from app.services.channel_import import (
    NormalizedChannelOrder,
    NormalizedLine,
    import_channel_order,
)

logger = logging.getLogger(__name__)

# Inventory scopes power stock write-back. Fulfillment-order scopes let the
# warehouse mark a Shopify order fulfilled only after its Veloyd label matched.
# We need both merchant-managed and third-party scopes because Shopify may route
# an order to a location managed by another app (for example Veloyd). Existing
# installs must reconnect to grant newly-added scopes.
OAUTH_SCOPES = (
    "read_orders,read_all_orders,read_products,"
    "read_locations,read_inventory,write_inventory,"
    "write_merchant_managed_fulfillment_orders,"
    "write_third_party_fulfillment_orders"
)
_SHOP_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com$")


def is_valid_shop_domain(shop: str) -> bool:
    """Guard against open-redirect / SSRF: only accept *.myshopify.com hosts."""
    return bool(shop and _SHOP_DOMAIN_RE.match(shop))


def build_authorize_url(shop: str, redirect_uri: str, state: str) -> str:
    """Shopify OAuth authorize URL the merchant is redirected to."""
    params = urlencode(
        {
            "client_id": settings.shopify_api_key,
            "scope": OAUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://{shop}/admin/oauth/authorize?{params}"


def verify_oauth_hmac(query_params: dict[str, str]) -> bool:
    """Verify the HMAC Shopify appends to the OAuth callback.

    HMAC-SHA256 of the sorted query string (excluding hmac/signature) keyed on
    the app's client secret.
    """
    received = query_params.get("hmac")
    if not received or not settings.shopify_api_secret:
        return False
    pairs = sorted(
        f"{k}={v}" for k, v in query_params.items() if k not in ("hmac", "signature")
    )
    message = "&".join(pairs)
    digest = hmac.new(
        settings.shopify_api_secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, received)


def exchange_code_for_token(shop: str, code: str) -> dict:
    """Exchange the OAuth authorization code for an Admin API access token."""
    resp = httpx.post(
        f"https://{shop}/admin/oauth/access_token",
        json={
            "client_id": settings.shopify_api_key,
            "client_secret": settings.shopify_api_secret,
            "code": code,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()  # {"access_token": "...", "scope": "..."}

# GraphQL: orders updated since a watermark, newest activity first, with the
# variant barcode (EAN) per line.
_ORDERS_QUERY = """
query($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, sortKey: UPDATED_AT, query: $query) {
    edges {
      node {
        id
        name
        createdAt
        updatedAt
        cancelledAt
        displayFinancialStatus
        displayFulfillmentStatus
        shippingAddress { name }
        lineItems(first: 100) {
          edges {
            node {
              id
              quantity
              currentQuantity
              unfulfilledQuantity
              title
              variant { barcode sku }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Follow-up query to page through an order's remaining line items (>100).
_LINE_ITEMS_QUERY = """
query($id: ID!, $after: String) {
  order(id: $id) {
    lineItems(first: 100, after: $after) {
      edges {
        node {
          id
          quantity
          currentQuantity
          unfulfilledQuantity
          title
          variant { barcode sku }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _gid_to_id(gid: str) -> str:
    """'gid://shopify/Order/12345' -> '12345'."""
    return gid.rsplit("/", 1)[-1] if gid else gid


def _normalize_reference(name: str | None) -> str | None:
    """Shopify order name '#1262' -> '1262'.

    This is the human order number, distinct from the internal order id in
    external_id. The courier's label (Veloyd) carries it as its reference, so we
    store the normalized form (no leading '#', trimmed) to match against later.
    """
    if not name:
        return None
    return name.strip().lstrip("#").strip() or None


def to_normalized(node: dict) -> NormalizedChannelOrder:
    """Map one Shopify order node to the internal normalized order. Pure."""
    # Customer name comes from the shipping address (covered by read_orders), so
    # the integration does not need the read_customers scope.
    ship = node.get("shippingAddress") or {}
    line_edges = (node.get("lineItems") or {}).get("edges") or []
    lines: list[NormalizedLine] = []
    for edge in line_edges:
        ln = edge.get("node") or {}
        variant = ln.get("variant") or {}
        lines.append(
            NormalizedLine(
                ean=(variant.get("barcode") or None),
                quantity=int(
                    ln.get("currentQuantity")
                    if ln.get("currentQuantity") is not None
                    else ln.get("quantity") or 0
                ),
                title=ln.get("title") or "",
                external_id=ln.get("id"),
                unfulfilled_quantity=(
                    int(ln["unfulfilledQuantity"])
                    if ln.get("unfulfilledQuantity") is not None
                    else None
                ),
            )
        )

    ordered_at = None
    if node.get("createdAt"):
        # Shopify timestamps are ISO-8601 with a trailing 'Z'.
        ordered_at = datetime.datetime.fromisoformat(
            node["createdAt"].replace("Z", "+00:00")
        )

    return NormalizedChannelOrder(
        external_id=_gid_to_id(node.get("id", "")),
        reference=_normalize_reference(node.get("name")),
        ordered_at=ordered_at,
        customer_name=ship.get("name"),
        financial_status=(node.get("displayFinancialStatus") or "pending").lower(),
        fulfillment_status=(node.get("displayFulfillmentStatus") or "unfulfilled").lower(),
        cancelled_at=node.get("cancelledAt"),
        lines=lines,
    )


# Inventory write-back queries/mutation. Resolve a variant's inventory_item_id
# by its barcode (== our EAN), the shop's primary active location, and push an
# absolute available quantity (idempotent, self-healing — a missed event never
# drifts because the next push overwrites with the truth).
_LOCATION_QUERY = """
query {
  locations(first: 1, query: "status:active") {
    edges { node { id } }
  }
}
"""

_VARIANT_BY_BARCODE_QUERY = """
query($q: String!) {
  productVariants(first: 1, query: $q) {
    edges { node { inventoryItem { id } } }
  }
}
"""

_SET_AVAILABLE_MUTATION = """
mutation($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    userErrors { field message }
  }
}
"""

_ORDER_FULFILLMENT_ORDERS_QUERY = """
query($id: ID!, $after: String) {
  order(id: $id) {
    displayFulfillmentStatus
    fulfillmentOrders(first: 100, after: $after) {
      nodes {
        id
        status
        supportedActions { action }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_FULFILLMENT_CREATE_MUTATION = """
mutation($fulfillment: FulfillmentInput!, $message: String) {
  fulfillmentCreate(fulfillment: $fulfillment, message: $message) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""


class ShopifyClient:
    """Minimal Shopify GraphQL Admin API client."""

    def __init__(
        self,
        shop_domain: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
    ) -> None:
        self.shop_domain = shop_domain or settings.shopify_shop_domain
        # No global-token fallback: a sync token must come from the org's own
        # OAuth connection (see sync_shopify).
        self.access_token = access_token or ""
        self.api_version = api_version or settings.shopify_api_version

    @property
    def configured(self) -> bool:
        return bool(self.shop_domain and self.access_token)

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    def _post(self, query: str, variables: dict) -> dict:
        resp = httpx.post(
            self.endpoint,
            headers={
                "X-Shopify-Access-Token": self.access_token,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]

    def fetch_orders(self, updated_after: str | None = None, page_size: int = 50):
        """Yield normalized orders updated since ``updated_after`` (ISO-8601),
        following pagination. ``updated_after`` is the connection cursor."""
        after = None
        query_filter = f"updated_at:>='{updated_after}'" if updated_after else None
        while True:
            data = self._post(
                _ORDERS_QUERY,
                {"first": page_size, "after": after, "query": query_filter},
            )
            conn = data["orders"]
            for edge in conn["edges"]:
                node = edge["node"]
                self._complete_line_items(node)
                yield node
            page = conn["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]

    def _complete_line_items(self, node: dict) -> None:
        """Page through an order's remaining line items so orders with >100 lines
        are imported whole instead of silently truncated."""
        li = node.get("lineItems") or {}
        page = li.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return
        edges = list(li.get("edges") or [])
        after = page.get("endCursor")
        while True:
            data = self._post(_LINE_ITEMS_QUERY, {"id": node["id"], "after": after})
            more = (data.get("order") or {}).get("lineItems") or {}
            edges.extend(more.get("edges") or [])
            mpage = more.get("pageInfo") or {}
            if not mpage.get("hasNextPage"):
                break
            after = mpage.get("endCursor")
        node["lineItems"]["edges"] = edges

    # --- Inventory write-back -------------------------------------------------

    def primary_location_id(self) -> str | None:
        """The shop's first active location gid, or None if none is exposed."""
        data = self._post(_LOCATION_QUERY, {})
        edges = (data.get("locations") or {}).get("edges") or []
        return edges[0]["node"]["id"] if edges else None

    def resolve_inventory_item_id(self, ean: str) -> str | None:
        """The inventory_item gid for the variant carrying ``ean`` as its barcode,
        or None if no such variant exists at the shop."""
        data = self._post(_VARIANT_BY_BARCODE_QUERY, {"q": f"barcode:{ean}"})
        edges = (data.get("productVariants") or {}).get("edges") or []
        if not edges:
            return None
        return ((edges[0]["node"] or {}).get("inventoryItem") or {}).get("id")

    def set_inventory_available(
        self, inventory_item_id: str, location_id: str, available: int
    ) -> None:
        """Overwrite the absolute ``available`` quantity at ``location_id``.

        ``ignoreCompareQuantity`` makes this an unconditional set (last write
        wins) — exactly what we want when our warehouse is the source of truth.
        Raises on Shopify userErrors so the caller can log a failed push.
        """
        data = self._post(
            _SET_AVAILABLE_MUTATION,
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "ignoreCompareQuantity": True,
                    "quantities": [
                        {
                            "inventoryItemId": inventory_item_id,
                            "locationId": location_id,
                            "quantity": available,
                        }
                    ],
                }
            },
        )
        errors = (data.get("inventorySetQuantities") or {}).get("userErrors") or []
        if errors:
            raise RuntimeError(f"Shopify inventory set error: {errors}")

    # --- Fulfillment write-back ----------------------------------------------

    def fulfill_order(
        self,
        external_order_id: str,
        *,
        notify_customer: bool = False,
        tracking_info: dict[str, str] | None = None,
    ) -> bool:
        """Fulfill every remaining fulfillment order for one Shopify order.

        Returns ``True`` when at least one fulfillment was created and ``False``
        when Shopify already considered the order fully fulfilled. Each
        fulfillment order is submitted separately because Shopify only permits
        fulfillment orders assigned to the same location in one mutation.

        Omitting line items intentionally fulfills every remaining item in each
        fulfillment order. The caller only invokes this after the local order is
        completely picked and its physical Veloyd label has matched.
        """
        order_gid = (
            external_order_id
            if external_order_id.startswith("gid://shopify/Order/")
            else f"gid://shopify/Order/{external_order_id}"
        )
        after = None
        fulfillment_status = None
        actionable_ids: list[str] = []

        while True:
            data = self._post(
                _ORDER_FULFILLMENT_ORDERS_QUERY,
                {"id": order_gid, "after": after},
            )
            order = data.get("order")
            if not order:
                raise RuntimeError("Shopify order niet gevonden")

            fulfillment_status = order.get("displayFulfillmentStatus")
            connection = order.get("fulfillmentOrders") or {}
            for fulfillment_order in connection.get("nodes") or []:
                actions = {
                    item.get("action")
                    for item in fulfillment_order.get("supportedActions") or []
                }
                if "CREATE_FULFILLMENT" in actions:
                    actionable_ids.append(fulfillment_order["id"])

            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")

        if fulfillment_status == "FULFILLED":
            return False
        if not actionable_ids:
            raise RuntimeError(
                "Shopify order heeft geen fulfillment die deze koppeling mag afronden"
            )

        for fulfillment_order_id in actionable_ids:
            fulfillment_input = {
                "lineItemsByFulfillmentOrder": [
                    {"fulfillmentOrderId": fulfillment_order_id}
                ],
                # Veloyd already handles shipment communication; avoid a
                # duplicate customer notification from Shopify.
                "notifyCustomer": notify_customer,
            }
            if tracking_info:
                fulfillment_input["trackingInfo"] = tracking_info

            data = self._post(
                _FULFILLMENT_CREATE_MUTATION,
                {
                    "fulfillment": fulfillment_input,
                    "message": "Veloyd-label gecontroleerd in Scan & Boek",
                },
            )
            payload = data.get("fulfillmentCreate") or {}
            errors = payload.get("userErrors") or []
            if errors:
                raise RuntimeError(f"Shopify fulfillment error: {errors}")
            if not payload.get("fulfillment"):
                raise RuntimeError("Shopify fulfillment gaf geen resultaat terug")

        return True


@dataclass
class SyncSummary:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unmatched: int = 0
    # SKUs whose reservation or physical stock changed during import; callers
    # mirror their new available quantity to Shopify.
    reserved_sku_ids: set[int] = field(default_factory=set)


def sync_shopify(db: Session, connection: ChannelConnection, client=None) -> SyncSummary:
    """Pull Shopify orders updated since the connection cursor and import each.

    Idempotent: the importer dedups on (org, channel, external_id), and the
    cursor is an ``updated_at`` watermark (``>=``), so the last order may be
    re-seen but never duplicated. Advances the cursor to the newest updatedAt.
    Does NOT commit — the caller owns the transaction.
    """
    # Serialize concurrent syncs of the same connection. A manual "sync now" and
    # the background poller (or two API replicas) can otherwise both re-see the
    # same order, both promote it observed/pending_product → active, and both
    # reserve its stock — a read-then-write race that double-reserves. Locking the
    # connection row makes the second sync wait for the first to commit, then
    # resume from the freshly-advanced cursor so it re-processes nothing.
    #
    # Flush our own pending writes first (e.g. the go-live mode/cursor change the
    # caller just made) so the locked re-read returns them, then populate_existing()
    # refreshes the in-session connection from the freshly-locked row — otherwise
    # the identity-mapped object would keep a stale cursor/mode from *before* we
    # waited on the lock, and we'd sync from an old cursor or an old mode. Postgres
    # takes a real row lock; SQLite (tests) ignores FOR UPDATE but still refreshes.
    db.flush()
    connection = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.id == connection.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if connection.mode == "live" and connection.inventory_authority_started_at is None:
        # Safe upgrade path for a connection that was already live before the
        # fulfillment-ledger migration: its current physical count is the
        # baseline, so only later fulfillment deltas may move stock.
        connection.inventory_authority_started_at = datetime.datetime.utcnow()

    # Strictly per-connection credentials: NEVER fall back to global env
    # credentials, or any org with the (default-on) channel_orders module could
    # pull the configured shop into its own tenant.
    client = client or ShopifyClient(
        shop_domain=connection.shop_domain,
        access_token=connection.access_token,
    )
    if not client.configured:
        raise RuntimeError("Shopify niet geconfigureerd")

    summary = SyncSummary()
    latest_updated = connection.cursor
    for node in client.fetch_orders(updated_after=connection.cursor):
        result = import_channel_order(db, connection, to_normalized(node))
        summary.fetched += 1
        if result.created:
            summary.created += 1
        else:
            summary.updated += 1
        summary.unmatched += len(result.unmatched_eans)
        summary.reserved_sku_ids.update(result.reserved_sku_ids)

        node_updated = node.get("updatedAt")
        if node_updated and (latest_updated is None or node_updated > latest_updated):
            latest_updated = node_updated

    if latest_updated:
        connection.cursor = latest_updated
    db.flush()
    return summary
