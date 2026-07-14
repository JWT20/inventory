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
        # Promote a parked order now that conditions allow: an order first seen in
        # observe-mode (pre-live) or parked in pending_product (missing catalog
        # entry) moves to its new target once the connection is live / the product
        # exists. Never downgrade or touch an order that already progressed
        # (active/completed/shipped/cancelled/closed).
        if (
            db_order.status in ("observed", "pending_product")
            and target_status in ("active", "pending_product")
        ):
            db_order.status = target_status
            became_active = target_status == "active"
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
    # order that already has bookings (a live-mode concern for fase 4).
    has_bookings = any(line.booked_count > 0 for line in db_order.lines)
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
    reserved_sku_ids: list[int] = []
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
    through a re-sync rather than a local relationship recompute. No-op when the
    org has no blocked order or no live connection. Does NOT commit.
    """
    if not ean:
        return False
    blocked = (
        db.query(Order)
        .filter(
            Order.organization_id == organization_id,
            Order.status == "pending_product",
        )
        .first()
    )
    if blocked is None:
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
