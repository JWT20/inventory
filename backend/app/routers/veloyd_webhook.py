"""The endpoint Veloyd posts a printed parcel to.

Public on purpose: Veloyd's webhook field takes a URL and offers no way to send
a header or sign a body. The secret is therefore the path, one per
organization, and an unknown one is answered with 404 — the same as a typo,
so a prober learns nothing from the difference.

Always answers 200 once the sender is known, whatever the body turned out to
be. Veloyd retries what it considers failed, and an event about a parcel that
is not ours is not a failure worth repeating.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import VeloydWebhookAck
from app.services.veloyd_webhook import apply_parcel_event, connection_for_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/veloyd", tags=["integrations"])


@router.post("/webhook/{token}", response_model=VeloydWebhookAck)
def receive_parcel_event(
    token: str,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> VeloydWebhookAck:
    connection = connection_for_token(db, token)
    if connection is None:
        raise HTTPException(404, "Onbekende webhook")

    result = apply_parcel_event(db, connection, payload)
    if result != "linked":
        # Only the uninteresting outcomes are logged: a linked parcel is
        # visible in the data itself.
        logger.info(
            "Veloyd-webhook voor organisatie %s: %s",
            connection.organization_id,
            result,
        )
    return VeloydWebhookAck(result=result)
