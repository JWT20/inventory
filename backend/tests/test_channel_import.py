"""Tests for the channel-agnostic order importer (Fase 3, PR 1).

import_channel_order upserts a channel order keyed on (org, channel,
external_id), resolves lines by EAN within the org, records matched/unmatched
for reconciliation, gives observe-mode orders the inert "observed" status, and
never moves stock.
"""
import json

from app.models import (
    ChannelConnection,
    ChannelSyncLog,
    Order,
    Organization,
    SKU,
    StockMovement,
)
from app.services.channel_import import (
    NormalizedChannelOrder,
    NormalizedLine,
    import_channel_order,
)
from tests.conftest import auth_header


def _org(db, slug, modules=("inventory", "orders", "barcode_picking", "channel_orders")):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _connection(db, org, mode="observe", channel="shopify"):
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


def _order(external_id="SHOP-1001", lines=None, customer="Webklant"):
    return NormalizedChannelOrder(
        external_id=external_id,
        customer_name=customer,
        financial_status="paid",
        lines=lines or [],
    )


def test_import_creates_observed_order_with_matched_lines(db):
    org = _org(db, "socks-import")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000001")
    _sku(db, org, "SOK-2", "8710000000002")

    order = _order(lines=[
        NormalizedLine(ean="8710000000001", quantity=2, title="Rode sok"),
        NormalizedLine(ean="8710000000002", quantity=1, title="Blauwe sok"),
    ])
    result = import_channel_order(db, conn, order)
    db.commit()

    assert result.created is True
    assert result.matched_lines == 2
    assert result.unmatched_eans == []

    db_order = db.get(Order, result.order_id)
    assert db_order.status == "observed"
    assert db_order.channel == "shopify"
    assert db_order.external_id == "SHOP-1001"
    assert len(db_order.lines) == 2
    assert {l.klant for l in db_order.lines} == {"Webklant"}


def test_unmatched_ean_is_reported_not_created(db):
    org = _org(db, "socks-unmatched")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000010")

    order = _order(external_id="SHOP-2001", lines=[
        NormalizedLine(ean="8710000000010", quantity=1),
        NormalizedLine(ean="9999999999999", quantity=3),  # niet in catalogus
    ])
    result = import_channel_order(db, conn, order)
    db.commit()

    assert result.matched_lines == 1
    assert result.unmatched_eans == ["9999999999999"]

    db_order = db.get(Order, result.order_id)
    assert len(db_order.lines) == 1  # only the matched line becomes an OrderLine

    log = db.query(ChannelSyncLog).filter_by(external_id="SHOP-2001").one()
    assert log.matched_lines == 1
    assert json.loads(log.unmatched_eans) == ["9999999999999"]
    assert log.action == "created"


def test_reimport_is_idempotent(db):
    org = _org(db, "socks-idem")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000020")

    order = _order(external_id="SHOP-3001",
                   lines=[NormalizedLine(ean="8710000000020", quantity=2)])
    first = import_channel_order(db, conn, order)
    db.commit()
    second = import_channel_order(db, conn, order)
    db.commit()

    assert first.created is True
    assert second.created is False
    assert first.order_id == second.order_id
    # No duplicate order for the same (org, channel, external_id).
    assert db.query(Order).filter_by(external_id="SHOP-3001").count() == 1
    assert len(db.get(Order, second.order_id).lines) == 1


def test_ean_resolves_within_order_org_only(db):
    org_a = _org(db, "socks-a")
    org_b = _org(db, "socks-b")
    conn_a = _connection(db, org_a)
    # Same EAN exists only in org B — must not be used for an org A order.
    _sku(db, org_b, "SOK-B", "8710000000030")

    order = _order(external_id="SHOP-4001",
                   lines=[NormalizedLine(ean="8710000000030", quantity=1)])
    result = import_channel_order(db, conn_a, order)
    db.commit()

    assert result.matched_lines == 0
    assert result.unmatched_eans == ["8710000000030"]


def test_import_moves_no_stock(db):
    org = _org(db, "socks-nostock")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000040")

    order = _order(external_id="SHOP-5001",
                   lines=[NormalizedLine(ean="8710000000040", quantity=5)])
    import_channel_order(db, conn, order)
    db.commit()

    assert db.query(StockMovement).count() == 0


def test_live_mode_creates_active_order(db):
    org = _org(db, "socks-live")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000050")

    order = _order(external_id="SHOP-6001",
                   lines=[NormalizedLine(ean="8710000000050", quantity=1)])
    result = import_channel_order(db, conn, order)
    db.commit()

    assert db.get(Order, result.order_id).status == "active"


def test_reimport_promotes_observed_to_active_when_live(db):
    org = _org(db, "socks-cutover")
    conn = _connection(db, org, mode="observe")
    _sku(db, org, "SOK-1", "8710000000070")
    order = _order(external_id="SHOP-9001",
                   lines=[NormalizedLine(ean="8710000000070", quantity=1)])

    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "observed"

    # Cutover: connection goes live, same order re-seen → becomes pickable.
    conn.mode = "live"
    db.commit()
    r2 = import_channel_order(db, conn, order)
    db.commit()
    assert r2.created is False
    assert db.get(Order, r2.order_id).status == "active"


def test_live_mode_non_paid_order_stays_observed(db):
    org = _org(db, "socks-refund")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000080")
    order = _order(external_id="SHOP-RF",
                   lines=[NormalizedLine(ean="8710000000080", quantity=1)])
    order.financial_status = "refunded"  # not fulfillable

    r = import_channel_order(db, conn, order)
    db.commit()
    # Must NOT become a born-active, pickable order despite live-mode.
    assert db.get(Order, r.order_id).status == "observed"


def test_live_mode_fulfilled_order_stays_observed(db):
    org = _org(db, "socks-shipped")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000090")
    order = _order(external_id="SHOP-SHIP",
                   lines=[NormalizedLine(ean="8710000000090", quantity=1)])
    order.fulfillment_status = "fulfilled"  # already shipped (e.g. from home)

    r = import_channel_order(db, conn, order)
    db.commit()
    # A paid order that Shopify already marks fulfilled must stay out of the pick
    # list, matching the observe "verzonden" badge — never picked twice.
    assert db.get(Order, r.order_id).status == "observed"


def test_observed_order_not_promoted_once_fulfilled(db):
    org = _org(db, "socks-cutover-shipped")
    conn = _connection(db, org, mode="observe")
    _sku(db, org, "SOK-1", "8710000000091")
    order = _order(external_id="SHOP-9100",
                   lines=[NormalizedLine(ean="8710000000091", quantity=1)])

    import_channel_order(db, conn, order)
    db.commit()

    # Cutover to live, but the order has meanwhile been shipped from home →
    # Shopify reports it fulfilled, so it must NOT be promoted to active.
    conn.mode = "live"
    order.fulfillment_status = "fulfilled"
    r2 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r2.order_id).status == "observed"


def test_ordered_at_is_persisted(db):
    import datetime
    org = _org(db, "socks-date")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000081")
    when = datetime.datetime(2026, 6, 1, 9, 0, 0)
    order = _order(external_id="SHOP-DT",
                   lines=[NormalizedLine(ean="8710000000081", quantity=1)])
    order.ordered_at = when

    r = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r.order_id).ordered_at == when


def test_sync_log_is_upserted_not_appended(db):
    org = _org(db, "socks-log")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000082")
    order = _order(external_id="SHOP-LOG",
                   lines=[NormalizedLine(ean="8710000000082", quantity=1)])

    import_channel_order(db, conn, order)
    db.commit()
    import_channel_order(db, conn, order)  # boundary re-import
    db.commit()

    logs = db.query(ChannelSyncLog).filter_by(external_id="SHOP-LOG").all()
    assert len(logs) == 1  # one row per order, not one per import
    assert logs[0].action == "updated"


def test_observed_orders_excluded_from_order_list(client, db, courier_token, owner_token):
    org = _org(db, "socks-listexcl")
    conn = _connection(db, org)
    _sku(db, org, "SOK-1", "8710000000060")
    import_channel_order(db, conn, _order(
        external_id="SHOP-7001",
        lines=[NormalizedLine(ean="8710000000060", quantity=1)],
    ))
    db.commit()

    # Courier sees the active/pending world, never observe-mode orders.
    resp = client.get("/api/orders", headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert all(o["status"] != "observed" for o in resp.json())


# --- pending_product: unknown-EAN blocking (mirror of pending_images) --------

def test_live_unmatched_order_parks_pending_product(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-block")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000200")  # only one of two is known
    order = _order(external_id="SHOP-BLK", lines=[
        NormalizedLine(ean="8710000000200", quantity=1),
        NormalizedLine(ean="8710000000201", quantity=2),  # unknown product
    ])
    r = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r.order_id)
    assert o.status == "pending_product"  # blocked, never active
    assert len(o.lines) == 1              # only the matched line is built
    assert r.unmatched_eans == ["8710000000201"]
    # Blocked order reserves nothing (not active).
    bal = db.query(InventoryBalance).filter_by(sku_id=sku.id).first()
    assert bal is None or bal.quantity_reserved == 0


def test_pending_product_self_heals_to_active_on_resync(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-heal")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000210")
    order = _order(external_id="SHOP-HEAL", lines=[
        NormalizedLine(ean="8710000000210", quantity=1),
        NormalizedLine(ean="8710000000211", quantity=1),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "pending_product"

    # Add the missing product; the same order re-seen now fully matches.
    sku2 = _sku(db, org, "SOK-2", "8710000000211")
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert r2.created is False
    assert o.status == "active"            # promoted
    assert len(o.lines) == 2               # both lines now built
    bal = db.query(InventoryBalance).filter_by(sku_id=sku2.id).first()
    assert bal is not None and bal.quantity_reserved == 1  # reserved on promote


def test_observe_mode_unmatched_stays_observed(db):
    org = _org(db, "socks-obs-block")
    conn = _connection(db, org, mode="observe")
    _sku(db, org, "SOK-1", "8710000000220")
    order = _order(external_id="SHOP-OBS", lines=[
        NormalizedLine(ean="8710000000220", quantity=1),
        NormalizedLine(ean="8710000000221", quantity=1),  # unknown
    ])
    r = import_channel_order(db, conn, order)
    db.commit()
    # In observe nothing is pickable anyway → plain observed, not pending_product.
    assert db.get(Order, r.order_id).status == "observed"


def test_resync_hook_resets_cursor_when_blocked_order_exists(db):
    from app.services.channel_import import resync_channel_for_new_ean

    org = _org(db, "socks-hook")
    conn = _connection(db, org, mode="live")
    conn.cursor = "2026-06-01T00:00:00Z"
    db.add(Order(organization_id=org.id, channel="shopify", external_id="X1",
                 reference="R1", status="pending_product"))
    db.commit()

    changed = resync_channel_for_new_ean(db, org.id, "8710000000300")
    db.commit()
    assert changed is True
    db.refresh(conn)
    assert conn.cursor is None  # forces a full re-sync so the order re-promotes


def test_resync_hook_noop_without_blocked_order(db):
    from app.services.channel_import import resync_channel_for_new_ean

    org = _org(db, "socks-hook-noop")
    conn = _connection(db, org, mode="live")
    conn.cursor = "2026-06-01T00:00:00Z"
    db.commit()

    changed = resync_channel_for_new_ean(db, org.id, "8710000000301")
    assert changed is False
    db.refresh(conn)
    assert conn.cursor == "2026-06-01T00:00:00Z"  # untouched
