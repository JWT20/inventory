"""Tests for the channel-agnostic order importer (Fase 3, PR 1).

import_channel_order upserts a channel order keyed on (org, channel,
external_id), resolves lines by EAN within the org, records matched/unmatched
for reconciliation, gives observe-mode orders the inert "observed" status, and
never moves stock.
"""
import datetime
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


def _blocked_order_with_unmatched(db, org, ean, external_id="X1"):
    """A pending_product order whose sync-log records `ean` as unmatched."""
    db.add(Order(organization_id=org.id, channel="shopify", external_id=external_id,
                 reference="R-" + external_id, status="pending_product"))
    db.add(ChannelSyncLog(organization_id=org.id, channel="shopify",
                          external_id=external_id, action="created", matched_lines=0,
                          unmatched_eans=json.dumps([ean])))
    db.commit()


def test_resync_hook_resets_cursor_when_order_waits_for_this_ean(db):
    from app.services.channel_import import resync_channel_for_new_ean

    org = _org(db, "socks-hook")
    conn = _connection(db, org, mode="live")
    conn.cursor = "2026-06-01T00:00:00Z"
    _blocked_order_with_unmatched(db, org, "8710000000300")

    changed = resync_channel_for_new_ean(db, org.id, "8710000000300")
    db.commit()
    assert changed is True
    db.refresh(conn)
    assert conn.cursor is None  # forces a full re-sync so the order re-promotes


def test_resync_hook_noop_for_unrelated_ean(db):
    from app.services.channel_import import resync_channel_for_new_ean

    org = _org(db, "socks-hook-unrelated")
    conn = _connection(db, org, mode="live")
    conn.cursor = "2026-06-01T00:00:00Z"
    # A blocked order exists, but it waits for a DIFFERENT ean.
    _blocked_order_with_unmatched(db, org, "8710000000300")

    changed = resync_channel_for_new_ean(db, org.id, "9999999999999")
    assert changed is False
    db.refresh(conn)
    assert conn.cursor == "2026-06-01T00:00:00Z"  # unrelated change → untouched


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


# --- P1 fix: an already-active order that gains an unknown line --------------

def test_active_order_gaining_unknown_ean_parks_and_releases(db):
    """No bookings yet → park to pending_product and release the reservation."""
    from app.models import InventoryBalance

    org = _org(db, "socks-active-block")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000400")
    order = _order(external_id="SHOP-AB", lines=[
        NormalizedLine(ean="8710000000400", quantity=2),
    ])
    # First import: fully matched, live → active + reserved.
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "active"
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2

    # Order edited at the channel: a new unknown product is added.
    order.lines.append(NormalizedLine(ean="8710000000401", quantity=1))
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "pending_product"          # no longer pickable
    assert sku.id in r2.reserved_sku_ids          # pushed after release
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 0


def test_active_order_with_bookings_gaining_unknown_goes_needs_review(db):
    """Picking already started → hand to a human, keep the booked work."""
    org = _org(db, "socks-active-booked")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000410")
    order = _order(external_id="SHOP-BK", lines=[
        NormalizedLine(ean="8710000000410", quantity=2),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    o = db.get(Order, r1.order_id)
    o.lines[0].booked_count = 1  # a unit has been picked
    db.commit()

    order.lines.append(NormalizedLine(ean="8710000000411", quantity=1))
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "needs_review"
    assert o.review_reason == "unknown_ean"
    assert len(o.lines) == 1              # booked line preserved, not rebuilt
    assert o.lines[0].booked_count == 1


# --- P2 fix: a blocked order later voided/fulfilled drops the block ----------

def test_pending_product_drops_block_when_no_longer_fulfillable(db):
    org = _org(db, "socks-block-refund")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000420")
    order = _order(external_id="SHOP-RF2", lines=[
        NormalizedLine(ean="8710000000420", quantity=1),
        NormalizedLine(ean="8710000000421", quantity=1),  # unknown → blocked
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "pending_product"

    # The order is refunded at the channel → no longer fulfillable.
    order.financial_status = "refunded"
    r2 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r2.order_id).status == "observed"  # block dropped


# --- blocker #3: an active order cancelled/refunded/fulfilled at the channel --

def test_active_order_cancelled_at_channel_is_released(db):
    """No bookings → cancel the order and release its reservation."""
    from app.models import InventoryBalance

    org = _org(db, "socks-cancel")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000500")
    order = _order(external_id="SHOP-CN", lines=[
        NormalizedLine(ean="8710000000500", quantity=2),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "active"
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2

    # Refunded at the channel → no longer fulfillable.
    order.financial_status = "refunded"
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "cancelled"                 # out of the pick list
    assert sku.id in r2.reserved_sku_ids           # pushed after release
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 0


def test_active_order_cancelled_with_bookings_goes_needs_review(db):
    """Picking already started → hand to a human, don't auto-cancel booked work."""
    org = _org(db, "socks-cancel-booked")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000510")
    order = _order(external_id="SHOP-CB", lines=[
        NormalizedLine(ean="8710000000510", quantity=2),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    o = db.get(Order, r1.order_id)
    o.lines[0].booked_count = 1
    db.commit()

    order.financial_status = "refunded"
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "needs_review"
    assert o.review_reason == "non_fulfillable_after_pick"
    assert o.lines[0].booked_count == 1  # booked work preserved


def test_active_order_fulfilled_elsewhere_goes_needs_review_without_raising_available(db):
    """Fulfilled elsewhere (shipped from home): do NOT release the reservation —
    that would raise `available` while the goods physically left. Park for review
    and leave on-hand/reserved (and thus available) untouched."""
    from app.models import InventoryBalance

    org = _org(db, "socks-fulfilled")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000520")
    # Seed a real on-hand so `available = on_hand - reserved` is meaningful.
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()

    order = _order(external_id="SHOP-FF", lines=[
        NormalizedLine(ean="8710000000520", quantity=2),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "active"
    bal = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert (bal.quantity_on_hand, bal.quantity_reserved) == (10, 2)  # available 8

    order.fulfillment_status = "fulfilled"
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "needs_review"          # NOT cancelled+released
    assert o.review_reason == "fulfilled_elsewhere"
    assert sku.id not in r2.reserved_sku_ids   # nothing pushed
    bal = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert (bal.quantity_on_hand, bal.quantity_reserved) == (10, 2)  # available still 8


def test_paid_but_cancelled_order_is_released(db):
    """A cancelled order can stay financial_status='paid' (cancelled without a
    refund). cancelledAt is the authoritative signal → it must leave the pick list."""
    from app.models import InventoryBalance

    org = _org(db, "socks-cancelled-paid")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000530")
    order = _order(external_id="SHOP-CX", lines=[
        NormalizedLine(ean="8710000000530", quantity=2),
    ])
    r1 = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r1.order_id).status == "active"

    # Cancelled at the channel WITHOUT a refund — financial_status stays paid.
    order.cancelled_at = "2026-07-14T10:00:00Z"
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "cancelled"
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 0


def test_born_cancelled_paid_order_stays_observed(db):
    """A paid+cancelled order seen for the first time in live mode must not become
    active (cancelledAt overrides the paid financial status)."""
    org = _org(db, "socks-born-cancelled")
    conn = _connection(db, org, mode="live")
    _sku(db, org, "SOK-1", "8710000000540")
    order = _order(external_id="SHOP-BC", lines=[
        NormalizedLine(ean="8710000000540", quantity=1),
    ])
    order.cancelled_at = "2026-07-14T10:00:00Z"
    r = import_channel_order(db, conn, order)
    db.commit()
    assert db.get(Order, r.order_id).status == "observed"


# --- pre-merge safety: exact reservation reconciliation ---------------------

def test_active_quantity_edit_updates_exact_reservation(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-quantity-edit")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000600")
    order = _order(external_id="SHOP-QTY", lines=[
        NormalizedLine(ean="8710000000600", quantity=2),
    ])
    first = import_channel_order(db, conn, order)
    db.commit()
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2

    order.lines[0].quantity = 3
    second = import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, first.order_id)
    assert saved.status == "active"
    assert [(line.sku_id, line.quantity) for line in saved.lines] == [(sku.id, 3)]
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 3
    assert second.reserved_sku_ids == [sku.id]


def test_active_product_replacement_moves_reservation_per_product(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-product-edit")
    conn = _connection(db, org, mode="live")
    old_sku = _sku(db, org, "OLD", "8710000000610")
    new_sku = _sku(db, org, "NEW", "8710000000611")
    order = _order(external_id="SHOP-SWAP", lines=[
        NormalizedLine(ean=old_sku.ean, quantity=2),
    ])
    result = import_channel_order(db, conn, order)
    db.commit()

    order.lines = [NormalizedLine(ean=new_sku.ean, quantity=2)]
    changed = import_channel_order(db, conn, order)
    db.commit()

    balances = {
        row.sku_id: row.quantity_reserved
        for row in db.query(InventoryBalance).filter(
            InventoryBalance.sku_id.in_([old_sku.id, new_sku.id])
        )
    }
    assert balances == {old_sku.id: 0, new_sku.id: 2}
    assert sorted(changed.reserved_sku_ids) == sorted([old_sku.id, new_sku.id])
    assert [(line.sku_id, line.quantity) for line in db.get(Order, result.order_id).lines] == [
        (new_sku.id, 2)
    ]


def test_active_quantity_edit_after_pick_is_parked_and_preserves_lines(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-picked-edit")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000620")
    order = _order(external_id="SHOP-PICKED-EDIT", lines=[
        NormalizedLine(ean=sku.ean, quantity=2),
    ])
    result = import_channel_order(db, conn, order)
    db.commit()
    saved = db.get(Order, result.order_id)
    saved.lines[0].booked_count = 1
    db.commit()

    order.lines[0].quantity = 3
    import_channel_order(db, conn, order)
    db.commit()

    db.refresh(saved)
    assert saved.status == "needs_review"
    assert saved.review_reason == "order_changed_after_pick"
    assert [(line.quantity, line.booked_count) for line in saved.lines] == [(2, 1)]
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2


# --- pre-merge safety: partial fulfilment gate ------------------------------

def test_born_partially_fulfilled_order_is_parked_without_reserving(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-born-partial")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000630")
    order = _order(external_id="SHOP-PARTIAL", lines=[
        NormalizedLine(ean=sku.ean, quantity=2),
    ])
    order.fulfillment_status = "partially_fulfilled"

    result = import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, result.order_id)
    assert saved.status == "needs_review"
    assert saved.review_reason == "partially_fulfilled"
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).first()
    assert balance is None or balance.quantity_reserved == 0


def test_active_order_becoming_partial_keeps_reservation_and_original_lines(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-active-partial")
    conn = _connection(db, org, mode="live")
    sku = _sku(db, org, "SOK-1", "8710000000640")
    order = _order(external_id="SHOP-ACTIVE-PARTIAL", lines=[
        NormalizedLine(ean=sku.ean, quantity=2),
    ])
    result = import_channel_order(db, conn, order)
    db.commit()

    order.fulfillment_status = "partially_fulfilled"
    order.lines[0].quantity = 1  # unsafe remote remainder; must not overwrite yet
    import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, result.order_id)
    assert saved.status == "needs_review"
    assert saved.review_reason == "partially_fulfilled"
    assert [line.quantity for line in saved.lines] == [2]
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2


# --- exact source lines: external/home fulfillment without locations --------

def _exact_line(ean, *, current, unfulfilled, line_id="gid://shopify/LineItem/1"):
    return NormalizedLine(
        ean=ean,
        quantity=current,
        external_id=line_id,
        unfulfilled_quantity=unfulfilled,
    )


def _authority_started(conn):
    conn.inventory_authority_started_at = datetime.datetime(2026, 7, 15, 10, 0)


def test_born_fulfilled_after_cutover_deducts_stock_once(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-born-home")
    conn = _connection(db, org, mode="live")
    _authority_started(conn)
    sku = _sku(db, org, "SOK-1", "8710000000700")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    order = _order(external_id="SHOP-HOME-BORN", lines=[
        _exact_line(sku.ean, current=2, unfulfilled=0),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 15, 10, 1)
    order.fulfillment_status = "fulfilled"

    first = import_channel_order(db, conn, order)
    db.commit()
    second = import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, first.order_id)
    line = saved.lines[0]
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert saved.status == "shipped"
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)
    assert (line.channel_fulfilled_seen, line.channel_fulfilled_external) == (2, 2)
    assert db.query(StockMovement).filter_by(
        reference_type="channel_fulfill", reference_id=saved.id
    ).count() == 1
    assert first.reserved_sku_ids == [sku.id]
    assert second.reserved_sku_ids == []  # idempotent: no second stock push/move


def test_active_order_fulfilled_externally_converts_reservation_to_stock(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-active-home")
    conn = _connection(db, org, mode="live")
    _authority_started(conn)
    sku = _sku(db, org, "SOK-1", "8710000000710")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    order = _order(external_id="SHOP-HOME-ACTIVE", lines=[
        _exact_line(sku.ean, current=2, unfulfilled=2),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 15, 10, 1)
    result = import_channel_order(db, conn, order)
    db.commit()
    assert db.query(InventoryBalance).filter_by(sku_id=sku.id).one().quantity_reserved == 2

    order.lines[0].unfulfilled_quantity = 0
    order.fulfillment_status = "fulfilled"
    import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, result.order_id)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert saved.status == "shipped"
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)
    assert saved.lines[0].quantity == 0


def test_partial_home_then_app_pick_reconciles_without_double_deduct(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-home-then-app")
    conn = _connection(db, org, mode="live")
    _authority_started(conn)
    sku = _sku(db, org, "SOK-1", "8710000000720")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    order = _order(external_id="SHOP-MIXED", lines=[
        _exact_line(sku.ean, current=2, unfulfilled=2),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 15, 10, 1)
    result = import_channel_order(db, conn, order)
    db.commit()

    # One unit leaves externally/home: physical stock and reservation both -1.
    order.lines[0].unfulfilled_quantity = 1
    order.fulfillment_status = "partially_fulfilled"
    import_channel_order(db, conn, order)
    db.commit()
    saved = db.get(Order, result.order_id)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert saved.status == "active"
    assert (saved.lines[0].quantity, saved.lines[0].booked_count) == (1, 0)
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (9, 1)

    # The remaining unit is picked in-app (simulate booking's atomic effects).
    saved.lines[0].booked_count = 1
    balance.quantity_on_hand = 8
    balance.quantity_reserved = 0
    db.commit()

    # Shopify later records that local unit too. It matches the app pick and must
    # not decrement physical stock a second time.
    order.lines[0].unfulfilled_quantity = 0
    order.fulfillment_status = "fulfilled"
    import_channel_order(db, conn, order)
    db.commit()
    db.refresh(saved)
    db.refresh(balance)
    line = saved.lines[0]
    assert saved.status == "shipped"
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)
    assert (line.channel_fulfilled_from_app, line.channel_fulfilled_external) == (1, 1)


def test_historical_fulfilled_order_is_baselined_not_deducted(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-home-baseline")
    conn = _connection(db, org, mode="live")
    _authority_started(conn)
    sku = _sku(db, org, "SOK-1", "8710000000730")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=8, quantity_reserved=0))
    db.commit()
    order = _order(external_id="SHOP-HISTORICAL", lines=[
        _exact_line(sku.ean, current=2, unfulfilled=0),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 15, 9, 59)
    order.fulfillment_status = "fulfilled"

    result = import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, result.order_id)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert saved.status == "observed"
    assert balance.quantity_on_hand == 8
    assert saved.lines[0].channel_fulfilled_seen == 2
    assert saved.lines[0].channel_fulfilled_external == 0
    assert db.query(StockMovement).filter_by(reference_id=saved.id).count() == 0


def test_fulfillment_counter_decrease_parks_without_auto_restock(db):
    from app.models import InventoryBalance

    org = _org(db, "socks-home-reversed")
    conn = _connection(db, org, mode="live")
    _authority_started(conn)
    sku = _sku(db, org, "SOK-1", "8710000000740")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    order = _order(external_id="SHOP-REVERSED", lines=[
        _exact_line(sku.ean, current=2, unfulfilled=0),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 15, 10, 1)
    order.fulfillment_status = "fulfilled"
    result = import_channel_order(db, conn, order)
    db.commit()

    order.lines[0].unfulfilled_quantity = 1
    order.fulfillment_status = "partially_fulfilled"
    import_channel_order(db, conn, order)
    db.commit()

    saved = db.get(Order, result.order_id)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert saved.status == "needs_review"
    assert saved.review_reason == "fulfillment_reversed"
    assert balance.quantity_on_hand == 8  # no guessed restock


# --- Finalize stamp: a sync may be the step that closes a picked order --------

def test_sync_shipping_a_picked_order_stamps_finalized(db):
    """Remote fulfillment of the remainder must not hide the local pick.

    Without the stamp the picked units never reach the monthly report, which
    selects on finalized_at.
    """
    from app.models import InventoryBalance

    org = _org(db, "socks-finalize")
    conn = _connection(db, org, mode="live")
    conn.inventory_authority_started_at = datetime.datetime(2026, 1, 1)
    sku = _sku(db, org, "SOK-1", "8710000000430")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id, quantity_on_hand=10))
    db.commit()

    order = _order(external_id="SHOP-FIN", lines=[
        NormalizedLine(
            ean="8710000000430",
            quantity=3,
            external_id="L1",
            unfulfilled_quantity=3,
        ),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 1)
    r1 = import_channel_order(db, conn, order)
    db.commit()
    o = db.get(Order, r1.order_id)
    assert o.status == "active"
    assert o.finalized_at is None

    o.lines[0].booked_count = 2  # two of three units picked in-app
    db.commit()

    # The remainder ships from home: the channel reports nothing unfulfilled.
    order.lines[0].unfulfilled_quantity = 0
    order.fulfillment_status = "fulfilled"
    r2 = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, r2.order_id)
    assert o.status == "shipped"
    assert o.finalized_at is not None


def test_sync_does_not_stamp_an_order_without_local_picks(db):
    """A purely external fulfillment is no warehouse work: no finalize stamp."""
    from app.models import InventoryBalance

    org = _org(db, "socks-no-finalize")
    conn = _connection(db, org, mode="live")
    conn.inventory_authority_started_at = datetime.datetime(2026, 1, 1)
    sku = _sku(db, org, "SOK-1", "8710000000431")
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id, quantity_on_hand=5))
    db.commit()
    order = _order(external_id="SHOP-EXT", lines=[
        NormalizedLine(
            ean="8710000000431",
            quantity=1,
            external_id="L1",
            unfulfilled_quantity=0,
        ),
    ])
    order.ordered_at = datetime.datetime(2026, 7, 1)
    order.fulfillment_status = "fulfilled"
    result = import_channel_order(db, conn, order)
    db.commit()

    o = db.get(Order, result.order_id)
    assert o.status == "shipped"
    assert o.finalized_at is None
