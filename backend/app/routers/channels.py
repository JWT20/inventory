"""Channel integration endpoints (Fase 3).

For now: an on-demand Shopify pull/backfill that imports orders in observe-mode.
A scheduled poll is wired up later; this endpoint lets an owner/admin run a sync
and (with the reconciliation view in PR 4) verify the result. Gated on the
``channel_orders`` module.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_module
from app.database import get_db
from app.models import ChannelConnection, User
from app.schemas import ChannelSyncSummary
from app.services.shopify import ShopifyClient, sync_shopify

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/channels",
    tags=["channels"],
    dependencies=[Depends(require_module("channel_orders"))],
)


def _resolve_org_id(user: User, requested: int | None) -> int:
    """Owner/member act on their own org; a platform admin must name the org."""
    if user.is_platform_admin:
        if requested:
            return requested
        raise HTTPException(400, "Platform admin moet organization_id opgeven")
    if user.organization_id:
        return user.organization_id
    raise HTTPException(400, "Gebruiker heeft geen organisatie")


def _get_or_create_connection(db: Session, org_id: int, channel: str) -> ChannelConnection:
    conn = (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == org_id,
            ChannelConnection.channel == channel,
        )
        .first()
    )
    if conn is None:
        # New connections start in observe-mode — never act by surprise.
        conn = ChannelConnection(organization_id=org_id, channel=channel, mode="observe")
        db.add(conn)
        db.flush()
    return conn


@router.post("/shopify/sync", response_model=ChannelSyncSummary)
def trigger_shopify_sync(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pull Shopify orders updated since the last sync and import them (observe)."""
    org_id = _resolve_org_id(user, organization_id)

    client = ShopifyClient()
    if not client.configured:
        raise HTTPException(400, "Shopify is niet geconfigureerd (ontbrekende credentials)")

    connection = _get_or_create_connection(db, org_id, "shopify")
    summary = sync_shopify(db, connection, client)
    db.commit()
    return ChannelSyncSummary(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unmatched=summary.unmatched,
    )
