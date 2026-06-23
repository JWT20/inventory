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
import logging
from dataclasses import dataclass

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
        displayFinancialStatus
        customer { displayName }
        lineItems(first: 100) {
          edges { node { quantity title variant { barcode sku } } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _gid_to_id(gid: str) -> str:
    """'gid://shopify/Order/12345' -> '12345'."""
    return gid.rsplit("/", 1)[-1] if gid else gid


def to_normalized(node: dict) -> NormalizedChannelOrder:
    """Map one Shopify order node to the internal normalized order. Pure."""
    customer = node.get("customer") or {}
    line_edges = (node.get("lineItems") or {}).get("edges") or []
    lines: list[NormalizedLine] = []
    for edge in line_edges:
        ln = edge.get("node") or {}
        variant = ln.get("variant") or {}
        lines.append(
            NormalizedLine(
                ean=(variant.get("barcode") or None),
                quantity=int(ln.get("quantity") or 0),
                title=ln.get("title") or "",
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
        ordered_at=ordered_at,
        customer_name=customer.get("displayName"),
        financial_status=(node.get("displayFinancialStatus") or "pending").lower(),
        lines=lines,
    )


class ShopifyClient:
    """Minimal Shopify GraphQL Admin API client."""

    def __init__(
        self,
        shop_domain: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
    ) -> None:
        self.shop_domain = shop_domain or settings.shopify_shop_domain
        self.access_token = access_token or settings.shopify_access_token
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
                yield edge["node"]
            page = conn["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]


@dataclass
class SyncSummary:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unmatched: int = 0


def sync_shopify(db: Session, connection: ChannelConnection, client=None) -> SyncSummary:
    """Pull Shopify orders updated since the connection cursor and import each.

    Idempotent: the importer dedups on (org, channel, external_id), and the
    cursor is an ``updated_at`` watermark (``>=``), so the last order may be
    re-seen but never duplicated. Advances the cursor to the newest updatedAt.
    Does NOT commit — the caller owns the transaction.
    """
    client = client or ShopifyClient()
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

        node_updated = node.get("updatedAt")
        if node_updated and (latest_updated is None or node_updated > latest_updated):
            latest_updated = node_updated

    if latest_updated:
        connection.cursor = latest_updated
    db.flush()
    return summary
