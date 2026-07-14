"""Channel-agnostic order import — the core of the observe-mode ingestion.

Adapters (Shopify in PR 2, bol later) translate an external order into a
``NormalizedChannelOrder`` and hand it to :func:`import_channel_order`. This
service knows nothing about any specific channel: it upserts an Order keyed on
``(organization, channel, external_id)`` (the dedup index from #358), resolves
each line's EAN to a SKU within the order's organization, and records what
matched / did not for the reconciliation view.

Observe-mode invariants: imported orders get the inert ``observed`` status, and
no stock ever moves here (``apply_stock_movement`` is never called). Cutover
(live mode) only changes the target status to ``active``.

Does NOT commit — the caller owns the transaction boundary, mirroring
``apply_booking`` / ``apply_stock_movement``.
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ChannelConnection, ChannelSyncLog, Order, OrderLine, SKU
from app.services.stock import adjust_reservation


@dataclass
class NormalizedLine:
    """One order line as produced by a channel adapter."""
    ean: str | None
    quantity: int
    title: str = ""


@dataclass
class NormalizedChannelOrder:
    """A channel order in the internal, channel-agnostic shape."""
    external_id: str
    # The channel's human order number (Shopify order name, e.g. "1262"),
    # normalized without '#'. Distinct from external_id (the internal order id).
    reference: str | None = None
    ordered_at: datetime.datetime | None = None
    customer_name: str | None = None
    # Channel financial state (paid / pending / cancelled / refunded …). Kept for
    # the reconciliation view and the later live-mode sync; observe-mode does not
    # act on it.
    financial_status: str = "pending"
    # Channel fulfillment state (fulfilled / unfulfilled / partially_fulfilled …).
    # Shown in observe so the operator sees which orders are already shipped (from
    # home or by the courier); the cutover will keep fulfilled orders out of the
    # pick list. Observe-mode does not act on it.
    fulfillment_status: str = "unfulfilled"
    lines: list[NormalizedLine] = field(default_factory=list)


@dataclass
class ImportResult:
    order_id: int
    created: bool
    matched_lines: int
    unmatched_eans: list[str]
    # SKUs whose stock got reserved because this order just went live (born-active
    # or cutover). The caller pushes their new available to Shopify.
    reserved_sku_ids: list[int] = field(default_factory=list)


# Shopify financial statuses that mean "this order should be fulfilled". Anything
# else (pending/refunded/voided/…) must never become a born-active, pickable order.
_FULFILLABLE_FINANCIAL_STATUSES = {"paid"}

# Shopify fulfillment statuses that mean "already shipped" — such orders must
# never become born-active/pickable, even in live mode, or the warehouse would
# pick something that already left (shipped from home, or labelled by the
# courier). Mirrors the observe UI's "verzonden" badge. Restock/cancellation is
# a separate concern (fase 4).
_SHIPPED_FULFILLMENT_STATUSES = {"fulfilled"}


def _release_order_reservation(db: Session, order, org_id: int) -> list[int]:
    """Release the reservation an active order was holding — its open (unbooked)
    quantity per SKU. Used when an active order is parked back to a blocked state.
    Returns the affected SKU ids so the caller can push the freshened available.
    """
    released: list[int] = []
    for line in order.lines:
        open_qty = line.quantity - line.booked_count
        if open_qty > 0:
            adjust_reservation(
                db, sku_id=line.sku_id, organization_id=org_id, delta=-open_qty
            )
            released.append(line.sku_id)
    return released


def import_channel_order(
    db: Session, connection: ChannelConnection, order: NormalizedChannelOrder
) -> ImportResult:
    """Upsert one channel order and record its EAN-match result.

    Idempotent on ``(organization, channel, external_id)``: re-importing the same
    order updates it instead of duplicating. Unmatched EANs are reported, never
    auto-created as products.
    """
    org_id = connection.organization_id
    channel = connection.channel
    fulfillable = order.financial_status in _FULFILLABLE_FINANCIAL_STATUSES
    already_shipped = order.fulfillment_status in _SHIPPED_FULFILLMENT_STATUSES

    # Resolve every line to a SKU (by EAN, within the org) up front — a pure read
    # pass — so the status decision below can see whether the order is fully
    # matched. An order with an unknown product must never silently activate with
    # that product dropped.
    matched_items: list[tuple[SKU, int]] = []
    unmatched: list[str] = []
    for nl in order.lines:
        sku = None
        if nl.ean:
            sku = (
                db.query(SKU)
                .filter(SKU.organization_id == org_id, SKU.ean == nl.ean)
                .first()
            )
        if sku is None:
            unmatched.append(nl.ean or "(geen EAN)")
        else:
            matched_items.append((sku, nl.quantity))
    matched = len(matched_items)
    has_unmatched = bool(unmatched)

    # Live-mode makes an order pickable only when it is fulfillable (paid), not
    # already shipped, AND every line matched a product. An order with an unknown
    # EAN parks in "pending_product" (visible as blocked, never in the pick list)
    # instead of activating with the missing product dropped — it self-heals to
    # active on a re-sync once the product is added. Non-live / unpaid / shipped /
    # cancelled orders stay inert ("observed"). Full cancellation→restock is fase 4.
    if connection.mode == "live" and fulfillable and not already_shipped:
        target_status = "pending_product" if has_unmatched else "active"
    else:
        target_status = "observed"

    existing = (
        db.query(Order)
        .filter(
            Order.organization_id == org_id,
            Order.channel == channel,
            Order.external_id == order.external_id,
        )
        .first()
    )
    created = existing is None

    # An order that crosses into "active" this import reserves its stock, so the
    # channel-visible available already excludes it (no oversell). Tracked here so
    # we reserve exactly once — never on a plain re-sync of an already-active order.
    became_active = False

    # Bookings mean the courier already started picking — never silently rebuild
    # or reconcile such an order.
    has_bookings = (not created) and any(
        line.booked_count > 0 for line in existing.lines
    )
    released_sku_ids: list[int] = []

    if created:
        db_order = Order(
            organization_id=org_id,
            channel=channel,
            external_id=order.external_id,
            # Unique internal reference; the channel's own order id lives in
            # external_id and is shown in the reconciliation view.
            reference=f"{channel[:3].upper()}-{uuid.uuid4().hex[:8].upper()}",
            channel_reference=order.reference,
            channel_fulfillment_status=order.fulfillment_status,
            status=target_status,
            ordered_at=order.ordered_at,
            created_by=None,
        )
        db.add(db_order)
        db.flush()
        became_active = target_status == "active"
    else:
        db_order = existing
        status = db_order.status
        if status in ("observed", "pending_product") and target_status in (
            "active",
            "pending_product",
        ):
            # Cutover (observed→active), product added (pending_product→active), or
            # still waiting (→pending_product). Reserves on reaching active.
            db_order.status = target_status
            became_active = target_status == "active"
        elif status == "pending_product" and target_status == "observed":
            # The blocked order later became non-fulfillable/fulfilled at the
            # channel → drop the block so it stops showing "Wacht op product" (and
            # stops triggering self-heal re-syncs).
            db_order.status = "observed"
        elif status == "active" and target_status == "observed":
            # The order was cancelled / refunded / unpaid, or fulfilled elsewhere
            # at the channel → it must leave the pick list. Release its reservation
            # and cancel it, unless picking already started (then a human decides).
            if has_bookings:
                db_order.status = "needs_review"
            else:
                released_sku_ids = _release_order_reservation(db, db_order, org_id)
                db_order.status = "cancelled"
        elif status == "active" and has_unmatched:
            # An already-active (still fulfillable) order gained an unknown line
            # (edited at the channel, or imported partially before this guard
            # existed). It must not stay pickable-but-incomplete.
            if has_bookings:
                # Picking already started — hand to a human, don't auto-reconcile
                # or drop the booked work.
                db_order.status = "needs_review"
            else:
                # Nothing picked yet: park as blocked and release its reservation
                # (it re-reserves if it self-heals back to active once the missing
                # product is added).
                released_sku_ids = _release_order_reservation(db, db_order, org_id)
                db_order.status = "pending_product"
        # active + fully matched + fulfillable, or terminal states: left untouched
        # (reserve once).
        if order.ordered_at is not None:
            db_order.ordered_at = order.ordered_at
        # Backfill / refresh the order number on re-import (e.g. for orders
        # imported before this column existed).
        if order.reference is not None:
            db_order.channel_reference = order.reference
        # Refresh fulfillment status on every re-sync: an order shipped from home
        # (or labelled by the courier) flips to "fulfilled" in Shopify, and the
        # observe view must reflect that.
        db_order.channel_fulfillment_status = order.fulfillment_status

    # Rebuild the matched lines on re-import from the up-front match pass. Safe
    # because parked orders (observed/pending_product) are inert; never touch an
    # order that already has bookings (needs_review / mid-pick).
    if not created and not has_bookings:
        for line in list(db_order.lines):
            db.delete(line)
        db.flush()

    rebuild_lines = created or not has_bookings
    if rebuild_lines:
        for sku, quantity in matched_items:
            db.add(
                OrderLine(
                    order_id=db_order.id,
                    sku_id=sku.id,
                    klant=order.customer_name or "",
                    customer_id=None,
                    quantity=quantity,
                )
            )

    # One sync-log row per order (upsert), not one per import: the watermark
    # cursor re-sees the boundary order every poll, which would otherwise grow
    # the table unbounded. The reconciliation view wants the current match state
    # per order anyway.
    log = (
        db.query(ChannelSyncLog)
        .filter(
            ChannelSyncLog.organization_id == org_id,
            ChannelSyncLog.channel == channel,
            ChannelSyncLog.external_id == order.external_id,
        )
        .first()
    )
    if log is None:
        log = ChannelSyncLog(
            organization_id=org_id, channel=channel, external_id=order.external_id
        )
        db.add(log)
    log.action = "created" if created else "updated"
    log.matched_lines = matched
    log.unmatched_eans = json.dumps(unmatched)
    log.synced_at = datetime.datetime.utcnow()
    connection.last_synced_at = datetime.datetime.utcnow()
    db.flush()

    # Reserve the freshly-active order's open quantity per SKU. The lines were
    # just rebuilt via db.add() (not appended to the cached collection), so on the
    # promote path db_order.lines is stale — expire it to reload the fresh rows,
    # otherwise a cutover would activate the order without reserving any stock.
    db.expire(db_order, ["lines"])
    # Released reservations (an active order parked back to blocked) also changed
    # the channel-visible available, so push them alongside any freshly reserved.
    reserved_sku_ids: list[int] = list(released_sku_ids)
    if became_active:
        for line in db_order.lines:
            open_qty = line.quantity - line.booked_count
            if open_qty > 0:
                adjust_reservation(
                    db, sku_id=line.sku_id, organization_id=org_id, delta=open_qty
                )
                reserved_sku_ids.append(line.sku_id)

    return ImportResult(
        order_id=db_order.id,
        created=created,
        matched_lines=matched,
        unmatched_eans=unmatched,
        reserved_sku_ids=reserved_sku_ids,
    )


def resync_channel_for_new_ean(
    db: Session, organization_id: int, ean: str | None
) -> bool:
    """Self-heal hook for the ``pending_product`` block.

    An order parked in ``pending_product`` waits for a missing catalog entry. When
    a product with an EAN is created/changed, flag the org's live channel
    connections for a full re-sync (reset the cursor) so the next sync re-sees the
    blocked order and — now that the product exists — promotes it to ``active``.

    Mirrors :func:`app.services.booking.promote_pending_images_orders_for_sku` in
    spirit; here the unmatched line lives only at the channel, so recovery runs
    through a re-sync rather than a local relationship recompute. Only fires when a
    blocked order is actually waiting for *this* EAN — otherwise every unrelated
    catalogue change would trigger a full-history re-sync. No-op when nothing is
    blocked on it or there is no live connection. Does NOT commit.
    """
    if not ean:
        return False
    # External ids of the org's currently-blocked orders.
    blocked_ext_ids = [
        row[0]
        for row in db.query(Order.external_id)
        .filter(
            Order.organization_id == organization_id,
            Order.status == "pending_product",
            Order.external_id.isnot(None),
        )
        .all()
    ]
    if not blocked_ext_ids:
        return False
    # Does any blocked order's recorded unmatched set contain this EAN?
    logs = (
        db.query(ChannelSyncLog)
        .filter(
            ChannelSyncLog.organization_id == organization_id,
            ChannelSyncLog.external_id.in_(blocked_ext_ids),
        )
        .all()
    )
    waiting = any(ean in json.loads(log.unmatched_eans or "[]") for log in logs)
    if not waiting:
        return False
    connections = (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == organization_id,
            ChannelConnection.mode == "live",
        )
        .all()
    )
    changed = False
    for conn in connections:
        if conn.cursor is not None:
            conn.cursor = None
            changed = True
    return changed
