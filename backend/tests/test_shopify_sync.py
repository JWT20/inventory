"""Tests for the Shopify adapter + sync runner (Fase 3, PR 2).

The mapping is pure (canned GraphQL nodes), and the sync runner takes an
injected client so no real HTTP happens. Orders land as observe-mode via the
shared importer.
"""
from app.config import settings
from app.models import ChannelConnection, Order, Organization, SKU
from app.services.shopify import OAUTH_SCOPES, SyncSummary, sync_shopify, to_normalized
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


def _node(order_id, barcode, qty=2, updated="2026-06-23T10:00:00Z",
          fulfillment="UNFULFILLED"):
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "name": f"#{order_id}",
        "createdAt": "2026-06-23T09:00:00Z",
        "updatedAt": updated,
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": fulfillment,
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

def test_oauth_scopes_include_full_order_history_access():
    """Full resync needs Shopify's protected all-orders scope, not just read_orders."""
    scopes = OAUTH_SCOPES.split(",")
    assert "read_orders" in scopes
    assert "read_all_orders" in scopes


def test_to_normalized_maps_barcode_to_ean():
    norm = to_normalized(_node("1001", "8710000000001", qty=3))
    assert norm.external_id == "1001"
    # The order name "#1001" is normalized to "1001" — the number Veloyd puts on
    # the label, which we match on at the later label-scan step.
    assert norm.reference == "1001"
    # Fulfillment status is carried through, normalized to lowercase.
    assert norm.fulfillment_status == "unfulfilled"
    assert norm.customer_name == "Web Klant"
    assert norm.financial_status == "paid"
    assert len(norm.lines) == 1
    assert norm.lines[0].ean == "8710000000001"
    assert norm.lines[0].quantity == 3


def test_to_normalized_maps_fulfilled_status():
    norm = to_normalized(_node("1003", "8710000000001", fulfillment="FULFILLED"))
    assert norm.fulfillment_status == "fulfilled"


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
    # The human order number ("#2001" -> "2001") is stored for the label match.
    assert {o.channel_reference for o in observed} == {"2001", "2002"}
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


def test_sync_stores_and_refreshes_fulfillment_status(db):
    org = _org(db, "socks-fulfil")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000003")

    # First seen as unfulfilled.
    sync_shopify(db, conn, FakeClient([_node("4001", "8710000000003")]))
    db.commit()
    order = db.query(Order).filter_by(organization_id=org.id, external_id="4001").one()
    assert order.channel_fulfillment_status == "unfulfilled"

    # Shipped (from home or by the courier) → next sync flips it to fulfilled.
    sync_shopify(
        db, conn,
        FakeClient([_node("4001", "8710000000003", updated="2026-06-23T12:00:00Z",
                          fulfillment="FULFILLED")]),
    )
    db.commit()
    db.refresh(order)
    assert order.channel_fulfillment_status == "fulfilled"


def test_sync_passes_cursor_to_client(db):
    org = _org(db, "socks-cursor")
    conn = _connection(db, org)
    conn.cursor = "2026-06-20T00:00:00Z"
    db.commit()
    client = FakeClient([])

    sync_shopify(db, conn, client)
    assert client.seen_updated_after == "2026-06-20T00:00:00Z"


def test_full_resync_resets_cursor(client, db, monkeypatch, admin_token, sample_org):
    """full=true clears the cursor before syncing, so Shopify re-sends the whole
    history; the default (incremental) sync keeps it."""
    import app.routers.channels as channels_mod

    conn = ChannelConnection(
        organization_id=sample_org.id, channel="shopify", mode="observe",
        shop_domain="x.myshopify.com", access_token="shpat_x",
        cursor="2026-06-20T00:00:00Z",
    )
    db.add(conn)
    db.commit()

    seen = {}

    def fake_sync(db, connection, client):
        seen["cursor"] = connection.cursor  # cursor as seen by the sync runner
        return SyncSummary()

    monkeypatch.setattr(channels_mod, "sync_shopify", fake_sync)

    # Incremental: cursor preserved.
    client.post(
        f"/api/channels/shopify/sync?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert seen["cursor"] == "2026-06-20T00:00:00Z"

    # Full: cursor reset to None before the sync runs.
    client.post(
        f"/api/channels/shopify/sync?organization_id={sample_org.id}&full=true",
        headers=auth_header(admin_token),
    )
    assert seen["cursor"] is None


# --- endpoint gating -------------------------------------------------------

def test_sync_endpoint_400_when_not_configured(client, db, admin_token, sample_org):
    # sample_org has channel_orders but no Shopify connection token → 400, not 500.
    resp = client.post(
        f"/api/channels/shopify/sync?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 400


def test_sync_endpoint_403_for_customer(client, db, customer_user):
    from app.auth import create_token
    # Channel endpoints are platform-admin only; a customer is rejected.
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


def test_sync_uses_only_own_connection_token(client, db, monkeypatch, admin_token):
    """No global-credential fallback: even with a global shop domain configured,
    an org without its own OAuth token cannot sync."""
    monkeypatch.setattr(settings, "shopify_shop_domain", "racesokken.myshopify.com")
    org = _org(db, "other-tenant")  # channel_orders, but no OAuth connection
    resp = client.post(
        f"/api/channels/shopify/sync?organization_id={org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 400  # global creds are not used as a fallback


def test_admin_sync_blocked_for_org_without_channel_module(client, db, admin_token):
    # Platform admins bypass require_module and may target any org, so the target
    # org itself must be checked for the channel_orders module.
    org = _org(db, "no-channel-admin", modules=("inventory", "orders"))
    resp = client.post(
        f"/api/channels/shopify/sync?organization_id={org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 403


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


# --- new admin UI endpoints ------------------------------------------------

def test_status_reports_not_connected(client, db, admin_token, sample_org):
    resp = client.get(
        f"/api/channels/shopify/status?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_connect_url_returns_authorize_url(client, db, admin_token, sample_org, monkeypatch):
    monkeypatch.setattr(settings, "shopify_api_key", "key123")
    monkeypatch.setattr(settings, "shopify_shop_domain", "racesokken.myshopify.com")
    monkeypatch.setattr(settings, "domain", "dockscan.nl")
    resp = client.get(
        f"/api/channels/shopify/connect-url?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert "admin/oauth/authorize" in resp.json()["url"]
    assert "client_id=key123" in resp.json()["url"]


def test_connect_url_403_for_non_admin(client, db, owner_token, sample_org):
    resp = client.get(
        f"/api/channels/shopify/connect-url?organization_id={sample_org.id}",
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 403


def test_reconciliation_lists_unmatched_eans(client, db, admin_token, sample_org):
    from app.services.channel_import import (
        NormalizedChannelOrder,
        NormalizedLine,
        import_channel_order,
    )

    conn = ChannelConnection(organization_id=sample_org.id, channel="shopify", mode="observe")
    db.add(conn)
    db.commit()
    db.refresh(conn)
    import_channel_order(
        db, conn,
        NormalizedChannelOrder(
            external_id="R1", reference="1042", fulfillment_status="fulfilled",
            lines=[NormalizedLine(ean="9999999999999", quantity=1)]
        ),
    )
    db.commit()

    resp = client.get(
        f"/api/channels/shopify/reconciliation?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "9999999999999" in body["unmatched_eans"]
    assert len(body["orders"]) == 1
    assert body["orders"][0]["external_id"] == "R1"
    # The human order number is surfaced so the operator can eyeball it against
    # the Veloyd label before cutover.
    assert body["orders"][0]["channel_reference"] == "1042"
    # Fulfillment status is surfaced so already-shipped orders are visible.
    assert body["orders"][0]["channel_fulfillment_status"] == "fulfilled"


def test_sync_refreshes_stale_connection_under_lock(db):
    """A connection loaded before a concurrent commit must be re-read under the
    lock. A stale in-memory mode=live would otherwise activate orders after the
    connection was flipped back to observe elsewhere."""
    from tests.conftest import TestingSessionLocal

    org = _org(db, "stale-conn")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000012340")

    # Another transaction flips the connection back to observe and commits, while
    # db's identity-mapped `conn` still holds the stale mode=live.
    other = TestingSessionLocal()
    other.get(ChannelConnection, conn.id).mode = "observe"
    other.commit()
    other.close()
    assert conn.mode == "live"  # stale

    sync_shopify(db, conn, FakeClient([_node("7700", "8710000012340")]))
    db.commit()

    # populate_existing() under the lock refreshed mode → observe, so the order is
    # imported inert (observed), NOT activated from a stale mode.
    assert conn.mode == "observe"
    assert db.query(Order).filter_by(external_id="7700").one().status == "observed"
