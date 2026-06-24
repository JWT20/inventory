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
    lines: list[NormalizedLine] = field(default_factory=list)


@dataclass
class ImportResult:
    order_id: int
    created: bool
    matched_lines: int
    unmatched_eans: list[str]


# Shopify financial statuses that mean "this order should be fulfilled". Anything
# else (pending/refunded/voided/…) must never become a born-active, pickable order.
_FULFILLABLE_FINANCIAL_STATUSES = {"paid"}


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
    # Live-mode only makes an order pickable when it is actually fulfillable
    # (paid). Cancelled/refunded/unpaid orders stay inert ("observed") so we
    # never ship a cancelled order. Full cancellation→restock is fase 4.
    fulfillable = order.financial_status in _FULFILLABLE_FINANCIAL_STATUSES
    target_status = "active" if (connection.mode == "live" and fulfillable) else "observed"

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

    if created:
        db_order = Order(
            organization_id=org_id,
            channel=channel,
            external_id=order.external_id,
            # Unique internal reference; the channel's own order id lives in
            # external_id and is shown in the reconciliation view.
            reference=f"{channel[:3].upper()}-{uuid.uuid4().hex[:8].upper()}",
            channel_reference=order.reference,
            status=target_status,
            ordered_at=order.ordered_at,
            created_by=None,
        )
        db.add(db_order)
        db.flush()
    else:
        db_order = existing
        # Cutover: an order first imported in observe-mode must become pickable
        # once the connection goes live and the order is re-seen. Only promote
        # observed → active; never downgrade or touch an order that already
        # progressed (active/completed/cancelled/closed).
        if db_order.status == "observed" and target_status == "active":
            db_order.status = "active"
        if order.ordered_at is not None:
            db_order.ordered_at = order.ordered_at
        # Backfill / refresh the order number on re-import (e.g. for orders
        # imported before this column existed).
        if order.reference is not None:
            db_order.channel_reference = order.reference

    matched = 0
    unmatched: list[str] = []

    # Rebuild the matched lines on re-import. Safe because observe orders are
    # inert; never touch an order that already has bookings (a live-mode concern
    # for fase 4).
    has_bookings = any(line.booked_count > 0 for line in db_order.lines)
    if not created and not has_bookings:
        for line in list(db_order.lines):
            db.delete(line)
        db.flush()

    rebuild_lines = created or not has_bookings
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
            continue
        matched += 1
        if rebuild_lines:
            db.add(
                OrderLine(
                    order_id=db_order.id,
                    sku_id=sku.id,
                    klant=order.customer_name or "",
                    customer_id=None,
                    quantity=nl.quantity,
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

    return ImportResult(
        order_id=db_order.id,
        created=created,
        matched_lines=matched,
        unmatched_eans=unmatched,
    )
