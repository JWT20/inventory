"""Settling an advice-app hold against the pick that takes the bottles away.

A delivery order from the advice app reserves its bottles the moment the
customer pays, long before anyone walks into the warehouse. When the courier
later picks that order, the same bottles must leave stock exactly once: the hold
is released and the shelf is decremented in one step. Doing either half without
the other is what "reserved twice" and "sold twice" look like in practice.

Which shelf that is comes from the hold, not from the order. A hold may span the
shop and the webshop — they are one sellable pool in two places — while an
``Order`` can only name one. Reading the pools back off the reservation lines is
what makes a split hold pickable at all.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AdviceReservation, AdviceReservationLine, Order
from app.services.advice_channel import ADVICE_CHANNEL

# Which shelf a pick empties first. The webshop shelf exists to serve delivery
# orders, so it goes before the shop counter's stock — the same preference the
# reservation itself used when it took the bottles.
CONSUME_ORDER = ("webshop", "store", "warehouse")


def hold_for_order(db: Session, order: Order) -> AdviceReservation | None:
    """The advice hold belonging to this order, if there is one.

    The two are keyed by the same value on both sides: the advice app's own
    order id. No extra column links them, because that value is already unique
    per merchant in both tables and a second link could only ever disagree with
    the first.

    Returns None for anything that is not a live advice order — a manual order,
    a Shopify order, or an advice order whose hold was never made (the advice
    app may post an order without reserving). Callers fall back to the order's
    own pool then.
    """
    if order.channel != ADVICE_CHANNEL or not order.external_id:
        return None
    return (
        db.query(AdviceReservation)
        .filter(
            AdviceReservation.organization_id == order.organization_id,
            AdviceReservation.external_order_id == order.external_id,
        )
        .with_for_update()
        .first()
    )


def _lines_for(
    db: Session, reservation: AdviceReservation, sku_id: int
) -> list[AdviceReservationLine]:
    """This product's hold rows, emptiest shelf preference first."""
    rows = (
        db.query(AdviceReservationLine)
        .filter(
            AdviceReservationLine.reservation_id == reservation.id,
            AdviceReservationLine.sku_id == sku_id,
        )
        .with_for_update()
        .all()
    )
    return sorted(
        rows,
        key=lambda row: (
            CONSUME_ORDER.index(row.inventory_location)
            if row.inventory_location in CONSUME_ORDER
            else len(CONSUME_ORDER),
            row.id,
        ),
    )


def consume(
    db: Session, reservation: AdviceReservation, sku_id: int, quantity: int
) -> list[tuple[str, int]]:
    """Take ``quantity`` bottles out of the hold; return what came off which shelf.

    Never takes more than is held. A pick that outruns its hold — the advice app
    reserved three and the courier scans a fourth — gets the shortfall back as a
    remainder the caller books against the order's own pool, rather than a
    silent free bottle or a crash in the middle of the warehouse.

    Marks the reservation collected once nothing is left to hold: the goods have
    physically gone, and leaving it "active" would keep stock aside for an order
    that is already packed.
    """
    taken: list[tuple[str, int]] = []
    remaining = quantity
    for line in _lines_for(db, reservation, sku_id):
        if remaining <= 0:
            break
        portion = min(line.open_quantity, remaining)
        if portion <= 0:
            continue
        line.consumed_quantity += portion
        taken.append((line.inventory_location, portion))
        remaining -= portion
    _refresh_status(db, reservation)
    return taken


def restore(
    db: Session, reservation: AdviceReservation, sku_id: int, quantity: int
) -> list[tuple[str, int]]:
    """Put ``quantity`` bottles back into the hold — the inverse of :func:`consume`.

    Fills the shelves in the reverse order they were emptied, so booking and
    undoing a unit leave the hold exactly as it was. Anything that was never
    consumed cannot be given back, which is the case where a pick outran its
    hold: that surplus was booked against the order's pool and is restored
    there instead.
    """
    given: list[tuple[str, int]] = []
    remaining = quantity
    for line in reversed(_lines_for(db, reservation, sku_id)):
        if remaining <= 0:
            break
        portion = min(line.consumed_quantity, remaining)
        if portion <= 0:
            continue
        line.consumed_quantity -= portion
        given.append((line.inventory_location, portion))
        remaining -= portion
    _refresh_status(db, reservation)
    return given


def _refresh_status(db: Session, reservation: AdviceReservation) -> None:
    """Follow the hold's own rows: nothing left to hold means collected.

    Undoing the last pick puts the reservation back to active, so a courier who
    mis-scans does not leave a delivery order holding nothing.
    """
    db.flush()
    open_total = sum(
        line.open_quantity
        for line in db.query(AdviceReservationLine).filter(
            AdviceReservationLine.reservation_id == reservation.id
        )
    )
    if open_total == 0 and reservation.status == "active":
        reservation.status = "collected"
        reservation.collected_at = func.now()
    elif open_total > 0 and reservation.status == "collected":
        reservation.status = "active"
        reservation.collected_at = None


def was_picked(db: Session, reservation: AdviceReservation) -> bool:
    """Whether a pick has already taken part of this hold.

    The advice app must not settle a hold the warehouse has already emptied —
    that is the double count this whole module exists to prevent.
    """
    return any(
        line.consumed_quantity > 0
        for line in db.query(AdviceReservationLine).filter(
            AdviceReservationLine.reservation_id == reservation.id
        )
    )
