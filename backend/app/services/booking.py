"""Booking domain logic: the single source of truth for what a booking does
to an order line and to its order.

Both receiving call sites (``confirm_booking`` and ``book_more``) funnel their
write through :func:`apply_booking`, and the order-edit endpoints in the orders
router share :func:`recompute_order_status`. Keeping the invariant here means it
is enforced in exactly one place instead of being copy-pasted per endpoint.
"""
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Booking, Order, OrderLine
from app.services.stock import adjust_reservation, apply_stock_movement


@dataclass
class BookingResult:
    """Everything a caller needs to build a BookingResponse without re-querying."""

    last_booking_id: int
    last_booking_created_at: datetime
    booked_quantity: int
    remaining: int
    order_status: str
    order_completed: bool


def remaining_for_line(line: OrderLine) -> int:
    return max(0, line.quantity - line.booked_count)


def recompute_order_status(order: Order, lines: list[OrderLine]) -> None:
    """Recompute order status based on SKU images and booking progress.

    Pure helper — does NOT commit. Uses the supplied ``lines`` rather than
    ``order.lines`` so a caller that has freshly locked/loaded the lines is
    never bitten by a stale relationship collection.

    Orders awaiting approval stay pending_approval regardless of line edits;
    approval is an explicit merchant action.
    """
    if order.status in ("completed", "shipped", "cancelled", "closed", "pending_approval"):
        return
    all_have_images = all(len(l.sku.reference_images) > 0 for l in lines)
    all_booked = all(l.booked_count >= l.quantity for l in lines)
    if order.status == "active":
        if all_booked:
            order.status = "completed"
            order.mark_finalized()
        return
    # Approved but waiting for images: promote once every SKU has one.
    if all_have_images:
        order.status = "active"


def promote_pending_images_orders_for_sku(db: Session, sku_id: int) -> list[Order]:
    """Re-evaluate every ``pending_images`` order that contains ``sku_id``.

    Adding a reference image to a SKU can complete the "every line has an image"
    condition for an order that was parked in ``pending_images``. The image
    upload/scan paths only recompute ``SKU.active`` (see
    :func:`app.services.product_status.recompute_active`); without this the order
    stays stuck until some unrelated action (re-approve, line edit, booking)
    happens to recompute it — so the courier "can't pick" the order.

    Loads orders from ``db`` so lazy-loaded ``line.sku.reference_images`` reflects
    committed rows. Does NOT commit — the caller owns the transaction. Returns the
    orders whose status actually changed.
    """
    orders = (
        db.query(Order)
        .join(OrderLine, OrderLine.order_id == Order.id)
        .filter(Order.status == "pending_images", OrderLine.sku_id == sku_id)
        .distinct()
        .all()
    )
    changed: list[Order] = []
    for order in orders:
        # The image gate reads ``line.sku.reference_images``. A caller in the
        # same request may have loaded that collection (as empty) *before* it
        # added the new image row — setting ``sku_id`` alone does not append to
        # an already-loaded collection, so it would still read stale/empty.
        # Expire it so ``recompute_order_status`` re-reads from the DB and sees
        # the freshly-flushed image.
        for line in order.lines:
            db.expire(line.sku, ["reference_images"])
        before = order.status
        recompute_order_status(order, order.lines)
        if order.status != before:
            changed.append(order)
    return changed


def apply_booking(
    db: Session,
    *,
    order_id: int,
    order_line_id: int,
    sku_id: int,
    quantity: int,
    cap_remaining: int | None,
    scanned_by: int,
    scan_image_path: str | None,
    confidence: float | None,
) -> BookingResult:
    """Book ``quantity`` units against an order line, atomically.

    Orchestration boundary: locks the order's lines, validates against the
    locked state, clamps to what is actually bookable, creates the bookings,
    deducts stock, recomputes the order status, and commits. The caller owns
    only policy concerns (token validation, allocation-cap error shaping) and
    response building.
    """
    # 1. Lock + refresh the order's lines (identity-map values are otherwise stale).
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order_id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    db.refresh(order, with_for_update=True)

    # 2. Validate against the locked data — never trust caller input blindly.
    line = next((l for l in lines if l.id == order_line_id), None)
    if line is None or line.order_id != order_id:
        raise HTTPException(404, "Orderregel hoort niet bij deze order")
    if line.sku_id != sku_id:
        raise HTTPException(409, "SKU komt niet overeen met de orderregel")
    if order.status != "active":
        raise HTTPException(409, f"Order is niet boekbaar (status: {order.status})")

    # 3. Clamp on fresh values.
    allowed = remaining_for_line(line)
    if cap_remaining is not None:
        allowed = min(allowed, cap_remaining)
    quantity = min(quantity, allowed)
    if quantity <= 0:
        raise HTTPException(409, "Niets meer te boeken op deze regel")

    # 4. Create bookings + bump the counter.
    last_booking = None
    for _ in range(quantity):
        booking = Booking(
            order_id=order.id,
            order_line_id=line.id,
            sku_id=sku_id,
            scanned_by=scanned_by,
            scan_image_path=scan_image_path,
            confidence=confidence,
        )
        db.add(booking)
        last_booking = booking
    line.booked_count += quantity
    db.flush()  # server-default created_at is populated after this

    # 5. Release this channel order's reservation *before* deducting stock, then
    #    deduct. Order matters: a fully-reserved product has available == 0, so
    #    the stock guard would 409 unless the unit's reservation is freed first.
    #    Only channel orders reserve (at activation) — a manual order must never
    #    touch the shared reservation, or it would release stock a channel order
    #    still holds and cause overselling. Same transaction: a later stock 409
    #    rolls the release back too.
    if order.channel != "manual":
        adjust_reservation(
            db, sku_id=sku_id, organization_id=order.organization_id, delta=-quantity
        )
    apply_stock_movement(
        db,
        sku_id=sku_id,
        organization_id=order.organization_id,
        quantity=-quantity,
        movement_type="pick",
        reference_type="booking",
        reference_id=last_booking.id,
        performed_by=scanned_by,
    )

    # 6. Recompute status on the fresh lines, then commit.
    recompute_order_status(order, lines)
    db.commit()

    return BookingResult(
        last_booking_id=last_booking.id,
        last_booking_created_at=last_booking.created_at,
        booked_quantity=quantity,
        remaining=remaining_for_line(line),
        order_status=order.status,
        order_completed=order.status == "completed",
    )


@dataclass
class UndoResult:
    """Everything a caller needs to build an UndoScanResponse without re-querying."""

    order_id: int
    order_line_id: int
    sku_id: int
    remaining: int
    order_status: str


def undo_booking(db: Session, *, booking_id: int, performed_by: int) -> UndoResult:
    """Reverse a single booking: drop the unit, restock it, un-complete the order.

    The inverse of one ``apply_booking`` unit. Mirrors its discipline — locks the
    order's lines, mutates on fresh state, commits once. Restock is a *positive*
    ``pick`` movement so it nets out against the original pick in any report that
    sums pick movements.

    A shipped order is terminal: the courier cannot undo a unit once the label
    gate has closed the order.
    """
    # Lock the booking row first so two concurrent undos of the same unit
    # serialize here: the first deletes it, the second then finds it gone and is
    # rejected — without the lock both would restock the same unit.
    booking = (
        db.query(Booking)
        .filter(Booking.id == booking_id)
        .with_for_update()
        .first()
    )
    if not booking:
        raise HTTPException(409, "Deze boeking is al teruggedraaid")

    # Lock + refresh the order's lines, same as apply_booking.
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == booking.order_id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    order = db.get(Order, booking.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    db.refresh(order, with_for_update=True)

    if order.status == "shipped":
        raise HTTPException(409, "Order is al verzonden; terugdraaien niet mogelijk")

    line = next((l for l in lines if l.id == booking.order_line_id), None)
    if line is None:
        raise HTTPException(404, "Orderregel hoort niet bij deze boeking")
    if line.channel_fulfilled_from_app > 0:
        # Shopify has already confirmed at least one locally-picked unit as
        # fulfilled. Without a booking-level source mapping we cannot know
        # whether this particular scan is still reversible, so never risk
        # restocking a unit that Shopify considers shipped.
        raise HTTPException(
            409,
            "Shopify heeft deze orderregel al als verzonden verwerkt; "
            "ongedaan maken is niet meer veilig",
        )

    sku_id = booking.sku_id
    org_id = order.organization_id

    # Drop the unit and delete the booking row.
    line.booked_count = max(0, line.booked_count - 1)
    db.delete(booking)
    db.flush()

    # Restock (+1). Positive ``pick`` movement → nets out against the original.
    apply_stock_movement(
        db,
        sku_id=sku_id,
        organization_id=org_id,
        quantity=1,
        movement_type="pick",
        reference_type="booking_undo",
        reference_id=None,
        performed_by=performed_by,
    )
    # Re-reserve the unit the order still needs (inverse of the pick release).
    # Only for channel orders, mirroring apply_booking — a manual undo must not
    # inflate the shared reservation.
    if order.channel != "manual":
        adjust_reservation(db, sku_id=sku_id, organization_id=org_id, delta=1)

    # A completed order that is no longer fully booked reopens for picking; clear
    # the finalize stamp so the monthly report does not count a reverted order.
    if order.status == "completed":
        order.status = "active"
        order.finalized_at = None

    db.commit()

    return UndoResult(
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku_id,
        remaining=remaining_for_line(line),
        order_status=order.status,
    )
