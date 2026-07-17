"""Tests for the observe→live cutover endpoint (POST /channels/shopify/mode).

Going live flips the connection to ``live``, resets the sync cursor and re-imports
the order history so observed orders promote to ``active`` and reserve their
stock. The Shopify client is monkeypatched with a fake that yields canned nodes,
so no real HTTP happens.
"""
from app.models import (
    ChannelConnection,
    InventoryBalance,
    Order,
    Organization,
    SKU,
)
from tests.conftest import auth_header, store_test_channel_token


def _org(db, slug, modules=("inventory", "orders", "channel_orders")):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _conn(db, org, mode="observe", creds=True):
    conn = ChannelConnection(
        organization_id=org.id,
        channel="shopify",
        mode=mode,
        shop_domain="x.myshopify.com" if creds else None,
    )
    db.add(conn)
    db.flush()
    if creds:
        store_test_channel_token(db, conn)
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


def _node(order_id, barcode, qty=2):
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "name": f"#{order_id}",
        "createdAt": "2026-06-23T09:00:00Z",
        "updatedAt": "2026-06-23T10:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "shippingAddress": {"name": "Web Klant"},
        "lineItems": {
            "edges": [
                {"node": {"id": f"gid://shopify/LineItem/{order_id}-1",
                          "quantity": qty, "currentQuantity": qty,
                          "unfulfilledQuantity": qty,
                          "title": "Race sok",
                          "variant": {"barcode": barcode, "sku": "SOK-1"}}}
            ]
        },
    }


class FakeClient:
    """Stand-in for ShopifyClient — yields canned nodes, no HTTP."""
    configured = True

    def __init__(self, nodes):
        self._nodes = nodes

    def fetch_orders(self, updated_after=None, page_size=50):
        yield from self._nodes


def _patch_client(monkeypatch, nodes):
    monkeypatch.setattr(
        "app.routers.channels.ShopifyClient",
        lambda **kwargs: FakeClient(nodes),
    )


# --- validation / auth -----------------------------------------------------

def test_invalid_mode_rejected(client, db, admin_token):
    org = _org(db, "mode-bad")
    _conn(db, org)
    resp = client.post(
        f"/api/channels/shopify/mode?organization_id={org.id}",
        headers=auth_header(admin_token),
        json={"mode": "banaan"},
    )
    assert resp.status_code == 400


def test_go_live_requires_connection(client, db, admin_token):
    org = _org(db, "mode-noconn")
    _conn(db, org, creds=False)
    resp = client.post(
        f"/api/channels/shopify/mode?organization_id={org.id}",
        headers=auth_header(admin_token),
        json={"mode": "live"},
    )
    assert resp.status_code == 400


def test_go_live_requires_admin(client, db, courier_token):
    org = _org(db, "mode-noadmin")
    _conn(db, org)
    resp = client.post(
        f"/api/channels/shopify/mode?organization_id={org.id}",
        headers=auth_header(courier_token),
        json={"mode": "live"},
    )
    assert resp.status_code == 403


# --- cutover behaviour ------------------------------------------------------

def test_go_live_promotes_observed_order_and_reserves(client, db, admin_token, monkeypatch):
    """An order imported in observe-mode becomes active + reserved at go-live."""
    org = _org(db, "mode-cutover")
    conn = _conn(db, org, mode="observe")
    sku = _sku(db, org, "S1", "8710000030001")
    # An order already imported while observing: inert, no reservation yet.
    observed = Order(
        organization_id=org.id, channel="shopify", external_id="5001",
        reference="SHO-CUT01", channel_reference="5001", status="observed",
    )
    db.add(observed)
    db.commit()

    _patch_client(monkeypatch, [_node("5001", "8710000030001", qty=2)])

    resp = client.post(
        f"/api/channels/shopify/mode?organization_id={org.id}",
        headers=auth_header(admin_token),
        json={"mode": "live"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "live"

    db.refresh(conn)
    assert conn.mode == "live"
    db.refresh(observed)
    assert observed.status == "active"  # promoted by the cutover re-sync

    balance = (
        db.query(InventoryBalance)
        .filter(InventoryBalance.sku_id == sku.id)
        .first()
    )
    assert balance is not None and balance.quantity_reserved == 2


def test_back_to_observe_does_not_resync(client, db, admin_token, monkeypatch):
    org = _org(db, "mode-rollback")
    conn = _conn(db, org, mode="live")

    called = {"sync": False}

    def fake_sync(*args, **kwargs):
        called["sync"] = True
        raise AssertionError("sync must not run when switching to observe")

    monkeypatch.setattr("app.routers.channels.sync_shopify", fake_sync)

    resp = client.post(
        f"/api/channels/shopify/mode?organization_id={org.id}",
        headers=auth_header(admin_token),
        json={"mode": "observe"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "observe"
    assert called["sync"] is False
    db.refresh(conn)
    assert conn.mode == "observe"


# --- auto-sync poller -------------------------------------------------------

def test_autosync_only_touches_live_connections(db, monkeypatch):
    """The background poller syncs live connections and skips observe ones."""
    from app.services import autosync
    from tests.conftest import TestingSessionLocal

    # The poller opens its own session via app.database.SessionLocal; point it at
    # the in-memory test engine so it sees the committed connections.
    monkeypatch.setattr("app.services.autosync.SessionLocal", TestingSessionLocal)

    org_live = _org(db, "poll-live")
    org_obs = _org(db, "poll-observe")
    conn_live = _conn(db, org_live, mode="live")
    _conn(db, org_obs, mode="observe")

    synced_ids: list[int] = []

    class _Summary:
        reserved_sku_ids: set = set()

    def fake_sync(_db, connection, client=None):
        synced_ids.append(connection.id)
        return _Summary()

    monkeypatch.setattr("app.services.autosync.ShopifyClient",
                        lambda **kwargs: FakeClient([]))
    monkeypatch.setattr("app.services.autosync.sync_shopify", fake_sync)

    autosync._sync_live_connections_once()

    assert synced_ids == [conn_live.id]
