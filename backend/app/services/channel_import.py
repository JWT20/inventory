"""Channel-agnostic order import — the core of the observe-mode ingestion.

Adapters (Shopify in PR 2, bol later) translate an external order into a
``NormalizedChannelOrder`` and hand it to :func:`import_channel_order`. This
service knows nothing about any specific channel: it upserts an Order keyed on
``(organization, channel, external_id)`` (the dedup index from #358), resolves
each line's EAN to a SKU within the order's organization, and records what
matched / did not for the reconciliation view.

Observe-mode invariants: imported orders get the inert ``observed`` status and
no stock moves. In live mode, exact source-line quantities reconcile open
reservations and external/home fulfillments against the single physical stock
pool.

Does NOT commit — the caller owns the transaction boundary, mirroring
``apply_booking`` / ``apply_stock_movement``.
"""
from __future__ import annotations

import datetime
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ChannelConnection, ChannelSyncLog, Order, OrderLine, SKU
from app.services.stock import adjust_reservation, apply_stock_movement


@dataclass
class NormalizedLine:
    """One order line as produced by a channel adapter."""
    ean: str | None
    quantity: int
    title: str = ""
    external_id: str | None = None
    # Exact remaining quantity at the source. None keeps non-Shopify adapters on
    # the conservative legacy path.
    unfulfilled_quantity: int | None = None


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
    # Latest shipment timestamp at the source. Adapters that do not expose it
    # leave this unset; an unset value never erases a previously observed date.
    shipped_at: datetime.datetime | None = None
    # Shopify's cancelledAt (ISO timestamp) or None. The authoritative cancel
    # signal — an order can be cancelled without a refund, so financial_status
    # alone ("paid") does not reveal it.
    cancelled_at: str | None = None
    lines: list[NormalizedLine] = field(default_factory=list)


@dataclass
class ImportResult:
    order_id: int
    created: bool
    matched_lines: int
    unmatched_eans: list[str]
    # SKUs whose reservation or physical stock changed. The caller pushes their
    # new available to Shopify.
    reserved_sku_ids: list[int] = field(default_factory=list)


# Shopify financial statuses that mean "this order should be fulfilled". Anything
# else (pending/refunded/voided/…) must never become a born-active, pickable order.
_FULFILLABLE_FINANCIAL_STATUSES = {"paid"}

# Shopify fulfillment statuses that mean "already shipped". Exact line-level
# quantities keep these orders out of the pick list while reconciling newly
# fulfilled units. Adapters without exact line data remain conservative.
_SHIPPED_FULFILLMENT_STATUSES = {"fulfilled"}

# A partially fulfilled order is safe only with exact line-level remaining
# quantities. Otherwise park it rather than risk shipping the same unit twice.
_PARTIALLY_SHIPPED_FULFILLMENT_STATUSES = {"partially_fulfilled"}

REVIEW_UNKNOWN_EAN = "unknown_ean"
REVIEW_CANCELLED_AFTER_PICK = "cancelled_after_pick"
REVIEW_NON_FULFILLABLE_AFTER_PICK = "non_fulfillable_after_pick"
REVIEW_FULFILLED_ELSEWHERE = "fulfilled_elsewhere"
REVIEW_PARTIALLY_FULFILLED = "partially_fulfilled"
REVIEW_ORDER_CHANGED_AFTER_PICK = "order_changed_after_pick"
REVIEW_FULFILLMENT_REVERSED = "fulfillment_reversed"
REVIEW_FULFILLMENT_MISMATCH = "fulfillment_quantity_mismatch"

# Only these review causes may use the explicit cancel/restock decision. Other
# causes need a different workflow and must remain safely parked.
CANCELLATION_REVIEW_REASONS = frozenset(
    {REVIEW_CANCELLED_AFTER_PICK, REVIEW_NON_FULFILLABLE_AFTER_PICK}
)


def _quantities_by_sku(
    items: list[tuple[int, int]],
) -> dict[int, int]:
    quantities: dict[int, int] = defaultdict(int)
    for sku_id, quantity in items:
        quantities[sku_id] += max(0, quantity)
    return dict(quantities)


def _open_quantities_by_sku(lines: list[OrderLine]) -> dict[int, int]:
    return _quantities_by_sku(
        [(line.sku_id, line.quantity - line.booked_count) for line in lines]
    )


@dataclass
class _LinePlan:
    sku: SKU
    source: NormalizedLine
    existing: OrderLine | None
    desired_quantity: int
    fulfilled_seen: int
    fulfilled_from_app: int
    fulfilled_external: int
    external_delta: int = 0


def _on_or_after(
    value: datetime.datetime | None, boundary: datetime.datetime | None
) -> bool:
    """Compare source and DB timestamps without mixing aware/naive objects."""
    if value is None or boundary is None:
        return False
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if boundary.tzinfo is not None:
        boundary = boundary.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value >= boundary


def _line_plans(
    *,
    matched_items: list[tuple[SKU, NormalizedLine]],
    existing_lines: list[OrderLine],
    connection: ChannelConnection,
    order: NormalizedChannelOrder,
    created: bool,
    existing_status: str | None,
) -> tuple[list[_LinePlan], list[OrderLine], bool, bool]:
    """Match source lines to local lines and derive idempotent fulfillment deltas.

    Returns plans, removed local lines, whether an exact fulfillment counter went
    backwards, and whether an order edit is unsafe after picking started.
    """
    by_external = {
        line.channel_line_id: line
        for line in existing_lines
        if line.channel_line_id is not None
    }
    claimed: set[int] = set()
    plans: list[_LinePlan] = []
    reversed_fulfillment = False
    unsafe_picked_edit = False

    for sku, source in matched_items:
        local = by_external.get(source.external_id) if source.external_id else None
        if local is None:
            # Upgrade legacy rows without line ids when the SKU match is
            # unambiguous. Duplicate same-SKU legacy lines are rebuilt only when
            # nothing was picked; otherwise the edit is parked below.
            candidates = [
                line
                for line in existing_lines
                if line.id not in claimed and line.sku_id == sku.id
            ]
            if len(candidates) == 1:
                local = candidates[0]
        if local is not None:
            claimed.add(local.id)

        booked = local.booked_count if local is not None else 0
        exact = (
            source.external_id is not None
            and source.unfulfilled_quantity is not None
        )
        current = max(0, source.quantity)

        if not exact:
            if local is not None and booked > 0 and (
                local.sku_id != sku.id or local.quantity != current
            ):
                unsafe_picked_edit = True
            elif local is None and any(
                line.booked_count > 0 for line in existing_lines
            ):
                unsafe_picked_edit = True
            plans.append(
                _LinePlan(
                    sku=sku,
                    source=source,
                    existing=local,
                    desired_quantity=max(booked, current),
                    fulfilled_seen=local.channel_fulfilled_seen if local else 0,
                    fulfilled_from_app=(
                        local.channel_fulfilled_from_app if local else 0
                    ),
                    fulfilled_external=(
                        local.channel_fulfilled_external if local else 0
                    ),
                )
            )
            continue

        unfulfilled = min(current, max(0, source.unfulfilled_quantity or 0))
        remote_fulfilled = current - unfulfilled
        seen = local.channel_fulfilled_seen if local is not None else 0
        from_app = local.channel_fulfilled_from_app if local is not None else 0
        external = local.channel_fulfilled_external if local is not None else 0

        # Observe mode continuously baselines remote fulfillment without moving
        # stock. At go-live, a newly discovered historical order is also part of
        # the physical opening count. A post-cutover order is processed even when
        # first seen already fully fulfilled.
        before_authority = not _on_or_after(
            order.ordered_at, connection.inventory_authority_started_at
        )
        legacy_historical = (
            local is not None
            and local.channel_line_id is None
            and existing_status in ("observed", "pending_product")
            and before_authority
        )
        baseline = connection.mode != "live" or (
            (created and before_authority) or legacy_historical
        )
        external_delta = 0
        if baseline:
            seen = remote_fulfilled
        elif remote_fulfilled < seen:
            # A fulfillment was reversed/returned. Never guess that goods came
            # physically back; that requires the same explicit restock decision
            # as a picked cancellation.
            reversed_fulfillment = True
        else:
            delta = remote_fulfilled - seen
            local_unmatched = max(0, booked - from_app)
            matched_to_app = min(delta, local_unmatched)
            external_delta = delta - matched_to_app
            from_app += matched_to_app
            external += external_delta
            seen = remote_fulfilled

        local_unmatched_after = max(0, booked - from_app)
        desired = booked + max(0, unfulfilled - local_unmatched_after)

        if local is not None and booked > 0:
            if local.sku_id != sku.id:
                unsafe_picked_edit = True
            if (
                local.channel_current_quantity is not None
                and local.channel_current_quantity != current
            ):
                unsafe_picked_edit = True
        elif local is None and any(line.booked_count > 0 for line in existing_lines):
            unsafe_picked_edit = True

        plans.append(
            _LinePlan(
                sku=sku,
                source=source,
                existing=local,
                desired_quantity=max(booked, desired),
                fulfilled_seen=seen,
                fulfilled_from_app=from_app,
                fulfilled_external=external,
                external_delta=external_delta,
            )
        )

    removed = [line for line in existing_lines if line.id not in claimed]
    if any(line.booked_count > 0 for line in removed):
        unsafe_picked_edit = True
    return plans, removed, reversed_fulfillment, unsafe_picked_edit


def import_channel_order(
    db: Session, connection: ChannelConnection, order: NormalizedChannelOrder
) -> ImportResult:
    """Upsert one channel order and reconcile exact source fulfillment.

    Exact adapters identify each order line and report its current unfulfilled
    quantity. A newly fulfilled unit is first matched to an in-app pick (whose
    stock already moved); only the unmatched remainder becomes an automated
    physical stock movement. Observe mode baselines counters without moving stock.
    """
    org_id = connection.organization_id
    channel = connection.channel
    fulfillable = order.financial_status in _FULFILLABLE_FINANCIAL_STATUSES
    already_shipped = order.fulfillment_status in _SHIPPED_FULFILLMENT_STATUSES
    partially_shipped = (
        order.fulfillment_status in _PARTIALLY_SHIPPED_FULFILLMENT_STATUSES
    )
    is_cancelled = order.cancelled_at is not None

    matched_items: list[tuple[SKU, NormalizedLine]] = []
    unmatched: list[str] = []
    for source in order.lines:
        sku = None
        if source.ean:
            sku = (
                db.query(SKU)
                .filter(SKU.organization_id == org_id, SKU.ean == source.ean)
                .first()
            )
        if sku is None:
            unmatched.append(source.ean or "(geen EAN)")
        else:
            matched_items.append((sku, source))
    matched = len(matched_items)
    has_unmatched = bool(unmatched)
    exact_lines = bool(order.lines) and all(
        line.external_id is not None and line.unfulfilled_quantity is not None
        for line in order.lines
    )

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
    existing_lines: list[OrderLine] = []
    if existing is not None:
        existing_lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == existing.id)
            .with_for_update()
            .populate_existing()
            .all()
        )
        db.refresh(existing, with_for_update=True)

    plans, removed_lines, fulfillment_reversed, unsafe_picked_edit = _line_plans(
        matched_items=matched_items,
        existing_lines=existing_lines,
        connection=connection,
        order=order,
        created=created,
        existing_status=existing.status if existing is not None else None,
    )
    has_bookings = any(line.booked_count > 0 for line in existing_lines)
    remote_unfulfilled = sum(
        max(0, line.unfulfilled_quantity or 0) for line in order.lines
    )
    fully_fulfilled_exact = exact_lines and remote_unfulfilled == 0

    # Base lifecycle for a new/inert order. Existing active/review states add
    # safety policy below.
    if connection.mode != "live":
        target_status = "observed"
    elif is_cancelled or not fulfillable:
        target_status = "observed"
    elif has_unmatched:
        target_status = "pending_product"
    elif partially_shipped and not exact_lines:
        target_status = "needs_review"
    elif already_shipped and exact_lines and not fully_fulfilled_exact:
        target_status = "needs_review"
    elif already_shipped and not exact_lines:
        target_status = "observed"
    elif fully_fulfilled_exact:
        historical = created and not _on_or_after(
            order.ordered_at, connection.inventory_authority_started_at
        )
        target_status = "observed" if historical else "shipped"
    else:
        target_status = "active"

    old_status = existing.status if existing is not None else None
    old_reason = existing.review_reason if existing is not None else None
    final_status = target_status
    review_reason: str | None = (
        REVIEW_PARTIALLY_FULFILLED
        if target_status == "needs_review" and partially_shipped
        else REVIEW_FULFILLMENT_MISMATCH
        if target_status == "needs_review" and already_shipped and exact_lines
        else None
    )

    if old_status in ("cancelled", "closed"):
        final_status = old_status
    elif fulfillment_reversed:
        final_status = "needs_review"
        review_reason = REVIEW_FULFILLMENT_REVERSED
    elif has_unmatched and old_status in ("active", "completed", "needs_review"):
        if has_bookings:
            final_status = "needs_review"
            review_reason = REVIEW_UNKNOWN_EAN
        else:
            final_status = "pending_product"
    elif (is_cancelled or not fulfillable) and old_status in (
        "active",
        "completed",
        "needs_review",
    ):
        if has_bookings:
            final_status = "needs_review"
            review_reason = (
                REVIEW_CANCELLED_AFTER_PICK
                if is_cancelled
                else REVIEW_NON_FULFILLABLE_AFTER_PICK
            )
        else:
            final_status = "cancelled"
    elif unsafe_picked_edit and has_bookings:
        final_status = "needs_review"
        review_reason = REVIEW_ORDER_CHANGED_AFTER_PICK
    elif old_status == "active" and already_shipped and not exact_lines:
        final_status = "needs_review"
        review_reason = REVIEW_FULFILLED_ELSEWHERE
    elif old_status == "active" and partially_shipped and not exact_lines:
        final_status = "needs_review"
        review_reason = REVIEW_PARTIALLY_FULFILLED
    elif old_status == "needs_review":
        # Exact fulfillment data is the missing evidence for these two legacy
        # parked causes, so they may self-heal. Other causes remain explicitly
        # human-gated.
        if old_reason not in (
            REVIEW_PARTIALLY_FULFILLED,
            REVIEW_FULFILLED_ELSEWHERE,
        ):
            final_status = "needs_review"
            review_reason = old_reason
    elif old_status == "completed" and not fully_fulfilled_exact:
        final_status = "completed"
    elif old_status == "shipped":
        final_status = "shipped"

    if created:
        db_order = Order(
            organization_id=org_id,
            channel=channel,
            external_id=order.external_id,
            reference=f"{channel[:3].upper()}-{uuid.uuid4().hex[:8].upper()}",
            channel_reference=order.reference,
            channel_fulfillment_status=order.fulfillment_status,
            status=final_status,
            review_reason=review_reason,
            ordered_at=order.ordered_at,
            channel_shipped_at=order.shipped_at,
            created_by=None,
        )
        db.add(db_order)
        db.flush()
    else:
        db_order = existing
        db_order.status = final_status
        db_order.review_reason = review_reason if final_status == "needs_review" else None
        if order.ordered_at is not None:
            db_order.ordered_at = order.ordered_at
        if order.reference is not None:
            db_order.channel_reference = order.reference
        db_order.channel_fulfillment_status = order.fulfillment_status
        if order.shipped_at is not None and (
            db_order.channel_shipped_at is None
            or _on_or_after(order.shipped_at, db_order.channel_shipped_at)
        ):
            db_order.channel_shipped_at = order.shipped_at

    affected_sku_ids: list[int] = []
    if final_status != "needs_review":
        old_open = (
            _open_quantities_by_sku(existing_lines)
            if old_status in ("active", "needs_review")
            else {}
        )
        target_open = (
            _quantities_by_sku(
                [
                    (
                        plan.sku.id,
                        plan.desired_quantity
                        - (plan.existing.booked_count if plan.existing else 0),
                    )
                    for plan in plans
                ]
            )
            if final_status == "active"
            else {}
        )
        # Reservation changes happen before external stock deductions. This is
        # the same invariant as an in-app pick and keeps fully-reserved products
        # deductible without temporarily violating on_hand >= reserved.
        for sku_id in sorted(set(old_open) | set(target_open)):
            delta = target_open.get(sku_id, 0) - old_open.get(sku_id, 0)
            if delta:
                adjust_reservation(
                    db, sku_id=sku_id, organization_id=org_id, delta=delta
                )
                affected_sku_ids.append(sku_id)

    if final_status != "needs_review" or created:
        for plan in plans:
            local = plan.existing
            if local is None:
                local = OrderLine(order_id=db_order.id, sku_id=plan.sku.id)
                db.add(local)
                plan.existing = local
            local.sku_id = plan.sku.id
            local.klant = order.customer_name or ""
            local.customer_id = None
            local.quantity = plan.desired_quantity
            local.channel_line_id = plan.source.external_id
            local.channel_current_quantity = plan.source.quantity
            local.channel_unfulfilled_quantity = plan.source.unfulfilled_quantity
            local.channel_fulfilled_seen = plan.fulfilled_seen
            local.channel_fulfilled_from_app = plan.fulfilled_from_app
            local.channel_fulfilled_external = plan.fulfilled_external
        for line in removed_lines:
            db.delete(line)
        db.flush()

    if final_status != "needs_review":
        external_by_sku = _quantities_by_sku(
            [(plan.sku.id, plan.external_delta) for plan in plans]
        )
        for sku_id, quantity in sorted(external_by_sku.items()):
            if quantity:
                apply_stock_movement(
                    db,
                    sku_id=sku_id,
                    organization_id=org_id,
                    quantity=-quantity,
                    movement_type="fulfillment",
                    reference_type="channel_fulfill",
                    reference_id=db_order.id,
                    note="Extern/vanuit huis verzonden via Shopify",
                    performed_by=None,
                )
                affected_sku_ids.append(sku_id)

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
        reserved_sku_ids=sorted(set(affected_sku_ids)),
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
