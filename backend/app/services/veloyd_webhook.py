"""Take in what Veloyd reports about a parcel it has printed.

The carrier prints the label, and only at that moment does Veloyd assign a
track-and-trace value. Without this, Dockscan learns that value from the
courier's scan — which is after the customer's mail went out and after the
parcel stopped being cancellable.

Veloyd's webhook field takes a URL and nothing else: no header, no signature.
So the path is the credential. It carries a secret per organization, only the
digest of which is stored, and everything that arrives is treated as a claim
rather than as fact: an event may only touch a parcel that this organization
created itself.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CarrierConnection, OrderParcel
from app.services.veloyd import VELOYD_CARRIER, normalize_tracking_code

logger = logging.getLogger(__name__)

#: Long enough that guessing is hopeless, short enough to paste into a form.
_TOKEN_BYTES = 32


def hash_webhook_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_webhook_token(connection: CarrierConnection) -> str:
    """Mint a new secret and keep only its digest. Invalidates the previous one."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    connection.webhook_token_hash = hash_webhook_token(token)
    return token


def connection_for_token(db: Session, token: str) -> CarrierConnection | None:
    """Resolve the sender, or nothing. A wrong secret learns no more than that."""
    if not token:
        return None
    return (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.carrier == VELOYD_CARRIER,
            CarrierConnection.webhook_token_hash == hash_webhook_token(token),
        )
        .first()
    )


def _first_value(payload: dict, *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _parcel_body(payload: dict) -> dict:
    """Veloyd's own reads wrap the parcel; the webhook shape is undocumented.

    Rather than guess one layout and break on the other, look through the
    wrappers Veloyd uses elsewhere and fall back to the body itself.
    """
    for key in ("parcel", "data", "shipment"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def apply_parcel_event(
    db: Session, connection: CarrierConnection, payload: dict
) -> str:
    """Record a reported tracking code. Returns what happened, for the log.

    Deliberately forgiving about what it does not recognise: Veloyd retries a
    webhook it considers failed, and an event about someone else's parcel — the
    carrier's account holds several merchants — is not an error on our side. It
    is simply not ours, and is dropped.
    """
    body = _parcel_body(payload if isinstance(payload, dict) else {})
    parcel_id = _first_value(body, "id", "parcelId", "parcel_id")
    reported = _first_value(body, "trackTrace", "tracktrace", "track_trace")
    if not parcel_id:
        return "ignored_without_parcel_id"

    parcel = (
        db.query(OrderParcel)
        .join(OrderParcel.order)
        .filter(OrderParcel.veloyd_parcel_id == parcel_id)
        .first()
    )
    if parcel is None or parcel.order.organization_id != connection.organization_id:
        # Either another merchant in the carrier's account, or a parcel made
        # outside Dockscan. Both are none of our business.
        return "ignored_unknown_parcel"

    if not reported:
        return "ignored_without_tracking_code"

    tracking_code = normalize_tracking_code(reported)
    if not tracking_code:
        return "ignored_unusable_tracking_code"

    if parcel.tracking_code == tracking_code:
        # Veloyd repeats an event it is unsure about; saying yes again is the
        # cheapest way to make it stop.
        return "unchanged"

    parcel.tracking_code = tracking_code
    parcel.tracking_url = _first_value(body, "trackTraceLink", "trackTraceUrl") or None
    # The print is what assigns the code, so this is when the cancel window shut.
    parcel.label_printed_at = parcel.label_printed_at or datetime.datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        # The unique index says another parcel already carries this code. Veloyd
        # reusing one across parcels is not something we can resolve from here,
        # and refusing would only buy an endless retry.
        db.rollback()
        logger.warning(
            "Veloyd meldde trackingcode %s die al aan een andere doos hangt",
            tracking_code,
        )
        return "conflict"
    return "linked"
