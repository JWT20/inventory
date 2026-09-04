"""Register the boxes of an advice-app delivery order at the carrier.

Shopify and bol orders never pass through here: Veloyd's own webshop links
create those parcels. The advice app has no such link, so Dockscan is the only
thing that can put a delivery order in front of the carrier.

Creating a parcel is the one step in this flow that reaches outside and cannot
simply be redone. Two rules follow from that, and both are load-bearing:

* every parcel is committed the moment Veloyd accepts it, before the next one
  is asked for. A crash then leaves boxes that are known and cancellable,
  never a label nobody can find;
* the label itself is left to the carrier. ``parcel/label`` is what assigns the
  track-and-trace value, and it moves the parcel into a state Veloyd refuses to
  remove — so as long as Dockscan does not call it, a mistake stays fixable.
"""
from __future__ import annotations

import datetime
import logging
import math

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CarrierConnection, Order, OrderParcel
from app.services.advice_channel import ADVICE_CHANNEL
from app.services.veloyd import VeloydError, client_for_organization

logger = logging.getLogger(__name__)

#: Boxes hold six bottles unless the merchant's carrier row says otherwise.
DEFAULT_BOTTLES_PER_BOX = 6

#: Wine may not be handed to a minor, so every parcel carries the check. Veloyd
#: only offers it for Dutch deliveries, which is why a foreign address is
#: refused rather than quietly shipped without one.
AGE_CHECK_OPTION = "Leeftijdscheck 18+"

#: The statuses in which registering boxes is meaningful. An observed order must
#: reach nothing outside — that is what observing means. A ``pending_product``
#: order would ship short, and a shipped one is already gone.
SHIPPABLE_STATUSES = frozenset({"active", "completed"})

#: How long a claimed-but-unanswered box is assumed to be someone else's work in
#: progress. Past that it is treated as the debris of a crash, and retried.
PENDING_CLAIM_TIMEOUT = datetime.timedelta(minutes=5)


class AdviceShippingError(RuntimeError):
    """A safe, operator-facing failure while registering an order's boxes."""


def bottles_per_box(db: Session, organization_id: int | None) -> int:
    connection = (
        db.query(CarrierConnection)
        .filter(CarrierConnection.organization_id == organization_id)
        .first()
        if organization_id
        else None
    )
    if connection and connection.bottles_per_box > 0:
        return connection.bottles_per_box
    return DEFAULT_BOTTLES_PER_BOX


def required_parcel_count(order: Order, per_box: int) -> int:
    bottles = sum(line.quantity for line in order.lines)
    if bottles <= 0:
        raise AdviceShippingError("Order heeft geen flessen om te verzenden")
    return math.ceil(bottles / per_box)


def _veloyd_address(order: Order) -> dict[str, str]:
    address = order.delivery_address
    if address is None:
        raise AdviceShippingError("Order mist een bezorgadres")
    if address.country.upper() != "NL":
        raise AdviceShippingError(
            "Leeftijdscheck 18+ bestaat alleen voor Nederland; "
            "regel deze zending handmatig bij de vervoerder"
        )
    payload = {
        "name": address.recipient_name,
        "street": address.street,
        "nr": address.house_number,
        "addition": address.house_number_suffix or "",
        "postalCode": address.postal_code,
        "city": address.city,
        "country": address.country.upper(),
    }
    if address.phone:
        payload["phone"] = address.phone
    if address.email:
        # Veloyd owns the track-and-trace mail. Without an address it simply
        # sends none, which is why this stays optional.
        payload["email"] = address.email
    return payload


def create_parcels(db: Session, order: Order, *, client=None) -> list[OrderParcel]:
    """Register the boxes this order still needs, and return all of them.

    Every box is claimed in the database *before* Veloyd is called, and the
    unique ``(order_id, sequence)`` makes that claim the mutex: two callers
    racing on the same order — a retried webhook and an operator pressing the
    button — cannot both register box one, because the loser never reaches the
    carrier. Each row is then committed on its own, because a parcel that exists
    at the carrier but not here is the one outcome nobody can repair from this
    side.

    Idempotent on the boxes that already exist: a retry after a failed third
    parcel asks Veloyd only for the third.
    """
    if order.channel != ADVICE_CHANNEL:
        raise AdviceShippingError("Alleen advies-orders worden hier aangemeld")
    if order.status not in SHIPPABLE_STATUSES:
        raise AdviceShippingError(
            f"Order met status {order.status} wordt niet bij de vervoerder aangemeld"
        )
    if not order.channel_reference:
        raise AdviceShippingError("Order mist een referentie voor op het label")

    address = _veloyd_address(order)
    wanted = required_parcel_count(order, bottles_per_box(db, order.organization_id))
    rows = {
        parcel.sequence: parcel
        for parcel in db.query(OrderParcel)
        .filter(OrderParcel.order_id == order.id)
        .order_by(OrderParcel.sequence)
        .all()
    }

    veloyd = client or client_for_organization(
        db, order.organization_id, allow_legacy_fallback=False
    )
    for sequence in range(1, wanted + 1):
        parcel = rows.get(sequence)
        if parcel is not None and parcel.veloyd_parcel_id:
            continue
        if parcel is None:
            parcel = _claim(db, order, sequence)
            rows[sequence] = parcel
        elif not _claim_is_stale(parcel):
            raise AdviceShippingError(
                f"Doos {sequence} wordt op dit moment al aangemeld; probeer zo opnieuw"
            )
        else:
            # A claim nobody finished. Whatever happened, the box is not
            # registered here — and it may or may not exist at the carrier, so
            # say so loudly rather than leave the order unshippable.
            logger.warning(
                "Doos %s van order %s bleef onvoltooid; opnieuw aanmelden kan een "
                "dubbele zending bij de vervoerder opleveren",
                sequence,
                order.reference,
            )

        try:
            parcel_id = veloyd.create_parcel(
                address=address,
                reference=order.channel_reference,
                options=[AGE_CHECK_OPTION],
                comment=f"Doos {sequence} van {wanted} — {order.reference}",
            )
        except VeloydError as exc:
            # Give the claim back, so the next attempt is a clean one rather
            # than a five-minute wait on a box that was never registered.
            db.delete(parcel)
            db.commit()
            raise AdviceShippingError(str(exc)) from exc

        parcel.veloyd_parcel_id = parcel_id
        db.commit()

    return [rows[sequence] for sequence in sorted(rows)]


def _claim(db: Session, order: Order, sequence: int) -> OrderParcel:
    """Reserve one box, or refuse because another caller got there first."""
    parcel = OrderParcel(order_id=order.id, sequence=sequence)
    db.add(parcel)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AdviceShippingError(
            f"Doos {sequence} wordt op dit moment al aangemeld; probeer zo opnieuw"
        ) from exc
    return parcel


def _claim_is_stale(parcel: OrderParcel) -> bool:
    claimed_at = parcel.created_at
    if claimed_at is None:
        return True
    return datetime.datetime.utcnow() - claimed_at > PENDING_CLAIM_TIMEOUT


def create_parcels_best_effort(db: Session, order: Order) -> None:
    """Register the boxes without letting the carrier break the caller.

    Used where the advice app is waiting on the response: an order that Veloyd
    could not accept must still land in Dockscan, because the retry endpoint
    can finish the job while a lost order cannot be recovered at all.
    """
    try:
        create_parcels(db, order)
    except (AdviceShippingError, VeloydError):
        logger.warning(
            "Kon zending nog niet aanmelden bij Veloyd voor order %s",
            order.reference,
            exc_info=True,
        )
