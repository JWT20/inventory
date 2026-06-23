"""Tests for the Shopify adapter + sync runner (Fase 3, PR 2).

The mapping is pure (canned GraphQL nodes), and the sync runner takes an
injected client so no real HTTP happens. Orders land as observe-mode via the
shared importer.
"""
from app.models import ChannelConnection, Order, Organization, SKU
from app.services.shopify import SyncSummary, sync_shopify, to_normalized
from tests.conftest import auth_header


def _org(db, slug, modules=("inventory", "orders", "channel_orders")):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _connection(db, org, channel="shopify", mode="observe"):
    conn = ChannelConnection(organization_id=org.id, channel=channel, mode=mode)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def _sku(db, org, code, ean):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _node(order_id, barcode, qty=2, updated="2026-06-23T10:00:00Z"):
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "name": f"#{order_id}",
        "createdAt": "2026-06-23T09:00:00Z",
        "updatedAt": updated,
        "displayFinancialStatus": "PAID",
        "shippingAddress": {"name": "Web Klant"},
        "lineItems": {
            "edges": [
                {"node": {"quantity": qty, "title": "Race sok",
                          "variant": {"barcode": barcode, "sku": "SOK-1"}}}
            ]
        },
    }


class FakeClient:
    """Stand-in for ShopifyClient — yields canned nodes, no HTTP."""
    configured = True

    def __init__(self, nodes):
        self._nodes = nodes
        self.seen_updated_after = "unset"

    def fetch_orders(self, updated_after=None, page_size=50):
        self.seen_updated_after = updated_after
        yield from self._nodes


# --- mapping ---------------------------------------------------------------

def test_to_normalized_maps_barcode_to_ean():
    norm = to_normalized(_node("1001", "8710000000001", qty=3))
    assert norm.external_id == "1001"
    assert norm.customer_name == "Web Klant"
    assert norm.financial_status == "paid"
    assert len(norm.lines) == 1
    assert norm.lines[0].ean == "8710000000001"
    assert norm.lines[0].quantity == 3


def test_to_normalized_missing_barcode_is_none():
    node = _node("1002", barcode=None)
    norm = to_normalized(node)
    assert norm.lines[0].ean is None  # importer will report this as unmatched


# --- sync runner -----------------------------------------------------------

def test_sync_imports_orders_as_observed_and_advances_cursor(db):
    org = _org(db, "socks-sync")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000001")
    client = FakeClient([
        _node("2001", "8710000000001", updated="2026-06-23T10:00:00Z"),
        _node("2002", "8710000000001", updated="2026-06-23T11:00:00Z"),
    ])

    summary = sync_shopify(db, conn, client)
    db.commit()

    assert isinstance(summary, SyncSummary)
    assert summary.fetched == 2
    assert summary.created == 2
    assert summary.unmatched == 0
    # Both stored as observe-mode orders for this org.
    observed = db.query(Order).filter_by(organization_id=org.id, status="observed").all()
    assert len(observed) == 2
    # Cursor advanced to the newest updatedAt.
    db.refresh(conn)
    assert conn.cursor == "2026-06-23T11:00:00Z"


def test_sync_is_idempotent_on_rerun(db):
    org = _org(db, "socks-sync-idem")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000002")
    nodes = [_node("3001", "8710000000002")]

    sync_shopify(db, conn, FakeClient(nodes))
    db.commit()
    summary2 = sync_shopify(db, conn, FakeClient(nodes))
    db.commit()

    assert summary2.created == 0
    assert summary2.updated == 1
    assert db.query(Order).filter_by(organization_id=org.id).count() == 1


def test_sync_passes_cursor_to_client(db):
    org = _org(db, "socks-cursor")
    conn = _connection(db, org)
    conn.cursor = "2026-06-20T00:00:00Z"
    db.commit()
    client = FakeClient([])

    sync_shopify(db, conn, client)
    assert client.seen_updated_after == "2026-06-20T00:00:00Z"


# --- endpoint gating -------------------------------------------------------

def test_sync_endpoint_400_when_not_configured(client, db, owner_token):
    # owner_token's org (sample_org) has all modules incl. channel_orders, but no
    # Shopify credentials are set in the test env → 400, not 500.
    resp = client.post("/api/channels/shopify/sync", headers=auth_header(owner_token))
    assert resp.status_code == 400


def test_sync_endpoint_403_for_customer(client, db, customer_user):
    from app.auth import create_token
    # customer_user is in sample_org (has channel_orders), but customers must not
    # be able to import channel orders — require_merchant blocks them.
    token = create_token(customer_user.id)
    resp = client.post("/api/channels/shopify/sync", headers=auth_header(token))
    assert resp.status_code == 403


def test_fetch_orders_paginates_line_items(monkeypatch):
    from app.services.shopify import ShopifyClient

    sc = ShopifyClient(shop_domain="x.myshopify.com", access_token="t")
    responses = [
        {"orders": {"edges": [{"node": {
            "id": "gid://shopify/Order/1",
            "updatedAt": "2026-06-23T10:00:00Z",
            "lineItems": {
                "edges": [{"node": {"quantity": 1, "title": "a", "variant": {"barcode": "E1"}}}],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            },
        }}], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
        {"order": {"lineItems": {
            "edges": [{"node": {"quantity": 2, "title": "b", "variant": {"barcode": "E2"}}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}},
    ]
    monkeypatch.setattr(sc, "_post", lambda q, v: responses.pop(0))

    nodes = list(sc.fetch_orders())
    assert len(nodes) == 1
    barcodes = [e["node"]["variant"]["barcode"] for e in nodes[0]["lineItems"]["edges"]]
    assert barcodes == ["E1", "E2"]  # second page appended, nothing dropped


def test_sync_endpoint_403_without_channel_module(client, db):
    from app.auth import create_token, hash_password
    from app.models import User
    org = _org(db, "no-channel", modules=("inventory", "orders"))
    owner = User(username="nc-owner", email="nc@local",
                 hashed_password=hash_password("OwnerPass1!"), role="owner",
                 organization_id=org.id, is_verified=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)
    resp = client.post(
        "/api/channels/shopify/sync", headers=auth_header(create_token(owner.id))
    )
    assert resp.status_code == 403
