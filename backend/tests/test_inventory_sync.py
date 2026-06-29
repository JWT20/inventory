"""Tests for Shopify inventory write-back (app-leidende voorraadsync).

Exercises the core ``push_available`` against the test session with a fake
Shopify client, plus the admin bulk-push (start-alignment) endpoint.
"""
from app.models import ChannelConnection, InventoryBalance, Organization, SKU
from app.services.inventory_sync import push_available
from tests.conftest import auth_header


class FakeClient:
    """Records set_inventory_available calls; resolves predictable ids."""

    last: "FakeClient | None" = None

    def __init__(self, shop_domain=None, access_token=None, variant_known=True):
        self.shop_domain = shop_domain
        self.access_token = access_token
        self.variant_known = variant_known
        self.sets: list[tuple] = []
        FakeClient.last = self

    @property
    def configured(self):
        return bool(self.shop_domain and self.access_token)

    def primary_location_id(self):
        return "gid://shopify/Location/1"

    def resolve_inventory_item_id(self, ean):
        return f"gid://shopify/InventoryItem/{ean}" if self.variant_known else None

    def set_inventory_available(self, inventory_item_id, location_id, available):
        self.sets.append((inventory_item_id, location_id, available))


def _factory(variant_known=True):
    def make(shop_domain=None, access_token=None):
        return FakeClient(shop_domain, access_token, variant_known=variant_known)

    return make


def _org(db, slug="sync-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _live_conn(db, org):
    conn = ChannelConnection(
        organization_id=org.id,
        channel="shopify",
        mode="live",
        status="active",
        shop_domain="x.myshopify.com",
        access_token="tok",
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def _sku(db, org, code="S1", ean="8710000000001"):
    sku = SKU(sku_code=code, name=code, organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _balance(db, sku, org, on_hand, reserved=0):
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=on_hand, quantity_reserved=reserved))
    db.commit()


def test_push_sets_available_and_caches_ids(db):
    org = _org(db)
    conn = _live_conn(db, org)
    sku = _sku(db, org)
    _balance(db, sku, org, on_hand=10, reserved=3)

    pushed = push_available(db, sku.id, org.id, client_factory=_factory())
    assert pushed is True
    # available = on_hand - reserved
    assert FakeClient.last.sets == [
        ("gid://shopify/InventoryItem/8710000000001", "gid://shopify/Location/1", 7)
    ]
    # Cached in-session (the caller commits); assert without reloading.
    assert sku.shopify_inventory_item_id == "gid://shopify/InventoryItem/8710000000001"
    assert conn.shopify_location_id == "gid://shopify/Location/1"


def test_push_noop_for_product_without_ean(db):
    org = _org(db, "sync-noean")
    _live_conn(db, org)
    sku = SKU(sku_code="WINE", name="Wine", organization_id=org.id,
              product_type="vision", ean=None)
    db.add(sku)
    db.commit()
    db.refresh(sku)

    assert push_available(db, sku.id, org.id, client_factory=_factory()) is False


def test_push_noop_without_live_connection(db):
    org = _org(db, "sync-observe")
    conn = ChannelConnection(organization_id=org.id, channel="shopify", mode="observe",
                             status="active", shop_domain="x.myshopify.com", access_token="t")
    db.add(conn)
    sku = _sku(db, org, "S2", "8710000000002")
    _balance(db, sku, org, on_hand=5)
    db.commit()

    assert push_available(db, sku.id, org.id, client_factory=_factory()) is False


def test_push_noop_when_variant_unknown(db):
    org = _org(db, "sync-novariant")
    _live_conn(db, org)
    sku = _sku(db, org, "S3", "8710000000003")
    _balance(db, sku, org, on_hand=5)

    assert push_available(db, sku.id, org.id, client_factory=_factory(variant_known=False)) is False
    db.refresh(sku)
    assert sku.shopify_inventory_item_id is None  # nothing cached on miss


# --- Bulk-push endpoint (start-alignment) ---------------------------------


def test_bulk_push_pushes_all_ean_skus(client, db, admin_token, monkeypatch):
    org = _org(db, "bulk-ok")
    _live_conn(db, org)
    _sku(db, org, "B1", "8710000010001")
    _sku(db, org, "B2", "8710000010002")
    # A vision product without EAN must be excluded by the query, not pushed.
    db.add(SKU(sku_code="WINE", name="Wine", organization_id=org.id,
               product_type="vision", ean=None))
    db.commit()

    calls: list[int] = []

    def fake_push(_db, sku_id, _org_id):
        calls.append(sku_id)
        return True

    monkeypatch.setattr("app.routers.channels.push_available", fake_push)

    resp = client.post(
        f"/api/channels/shopify/push-inventory?organization_id={org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"total": 2, "pushed": 2, "skipped_no_variant": 0, "failed": 0}
    assert len(calls) == 2


def test_bulk_push_counts_skipped_and_failed(client, db, admin_token, monkeypatch):
    org = _org(db, "bulk-mixed")
    _live_conn(db, org)
    _sku(db, org, "M1", "8710000020001")  # pushed
    _sku(db, org, "M2", "8710000020002")  # skipped (no variant)
    _sku(db, org, "M3", "8710000020003")  # raises

    def fake_push(_db, sku_id, _org_id):
        sku = _db.get(SKU, sku_id)
        if sku.sku_code == "M2":
            return False
        if sku.sku_code == "M3":
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr("app.routers.channels.push_available", fake_push)

    resp = client.post(
        f"/api/channels/shopify/push-inventory?organization_id={org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.json() == {"total": 3, "pushed": 1, "skipped_no_variant": 1, "failed": 1}


def test_bulk_push_requires_admin(client, db, courier_token):
    org = _org(db, "bulk-noadmin")
    _live_conn(db, org)

    resp = client.post(
        f"/api/channels/shopify/push-inventory?organization_id={org.id}",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403


def test_bulk_push_requires_live_connection(client, db, admin_token):
    org = _org(db, "bulk-observe")
    db.add(ChannelConnection(organization_id=org.id, channel="shopify", mode="observe",
                             status="active", shop_domain="x.myshopify.com", access_token="t"))
    db.commit()

    resp = client.post(
        f"/api/channels/shopify/push-inventory?organization_id={org.id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 400
