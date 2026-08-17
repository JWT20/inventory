"""The advice app as a sales channel, in observe mode.

The advice app's delivery orders need to become real orders here, because
picking and the shipping-label gate both hang off an ``Order`` row. They must
*not* become work yet: nobody has agreed a delivery flow, and stock is still
reserved through ``advice_reservations``.

``ChannelConnection.mode`` is the existing answer to exactly that problem. It is
what keeps imported Shopify and bol orders inert until an operator flips the
cutover, and it is why an advice order can be visible in Kanalen while staying
out of Scan & Boek, the week planning and the order list — those all read the
order list, which filters the ``observed`` status out (see
``routers/orders.py``). Inventing a second switch beside it would only mean two
places to get wrong.

The row itself carries no credentials. The advice app authenticates per request
with its own API key, so there is nothing to store and nothing to encrypt. The
loops that act on channels also cannot pick this row up by accident: the stock
push resolves a live connection per channel by name
(``services/inventory_sync.py``) and the periodic sync skips any channel it does
not recognise (``services/autosync.py``).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChannelConnection, Organization, User

#: The channel name for advice-app orders, as stored on ``Order.channel`` and
#: ``ChannelSyncLog.channel``. Deliberately not "wijnadvies": the surrounding
#: code, config and API keys all call this integration "advice".
ADVICE_CHANNEL = "advice"


class AdviceChannelNotObserving(RuntimeError):
    """The advice connection is live, and nothing here implements live yet."""


def resolve_advice_organization(
    db: Session, user: User, requested_org_id: int | None
) -> int:
    """Owner/member see their own merchant; platform admins the advice one.

    There is exactly one organization bound to the advice app, so an admin who
    names none gets that one rather than an error about a choice with a single
    possible answer. Shared by every read-only advice view, so they cannot drift
    apart on who may see what.
    """
    if user.is_platform_admin:
        org_id = requested_org_id or settings.advice_stock_organization_id
        if not org_id:
            raise HTTPException(400, "Geen advies-organisatie geconfigureerd")
        if not db.get(Organization, org_id):
            raise HTTPException(404, "Organisatie niet gevonden")
        return org_id

    if not user.organization_id:
        raise HTTPException(403, "Geen toegang tot de wijnadvies-koppeling")
    if requested_org_id and requested_org_id != user.organization_id:
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    return user.organization_id


def advice_connection(db: Session, organization_id: int) -> ChannelConnection:
    """Return the advice channel connection, creating it in observe mode.

    Created on first use rather than seeded: the advice app is the only thing
    that brings orders in, so the connection exists exactly when it is needed.
    Never acts by surprise — a new connection observes.

    Not flushed as a side effect of reading. The caller owns the transaction, the
    same way the reservation endpoints do.
    """
    connection = (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == organization_id,
            ChannelConnection.channel == ADVICE_CHANNEL,
        )
        .first()
    )
    if connection is None:
        connection = ChannelConnection(
            organization_id=organization_id,
            channel=ADVICE_CHANNEL,
            mode="observe",
        )
        db.add(connection)
        db.flush()
    return connection


def assert_advice_observing(connection: ChannelConnection) -> None:
    """Refuse to import while the connection claims to be live.

    Live means born-active, pickable orders whose reservation is consumed by the
    pick booking instead of at the counter. None of that is built. Setting the
    mode by hand would otherwise produce orders that reserve stock twice — once
    as an advice hold, once as a pick — and the discrepancy would only surface
    when a customer is standing at the door. Fail loudly instead.
    """
    if connection.mode != "observe":
        raise AdviceChannelNotObserving(
            "De wijnadvies-koppeling staat op live, maar live is nog niet gebouwd"
        )
