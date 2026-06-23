"""Channel integration endpoints (Fase 3).

Shopify connect (OAuth) + an on-demand pull that imports orders in observe-mode.
The OAuth callback is public (Shopify redirects the merchant's browser to it, so
it cannot carry our auth token); it is secured by Shopify's HMAC signature plus a
signed ``state`` that ties the install to the organization that started it.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_module
from app.config import settings
from app.database import get_db
from app.models import ChannelConnection, User
from app.schemas import ChannelSyncSummary
from app.services.shopify import (
    ShopifyClient,
    build_authorize_url,
    exchange_code_for_token,
    is_valid_shop_domain,
    sync_shopify,
    verify_oauth_hmac,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])

# Signs the OAuth state so the callback can trust which org started the install.
_state_signer = URLSafeTimedSerializer(settings.secret_key, salt="shopify-oauth")
_STATE_MAX_AGE = 600  # seconds


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


def _redirect_uri() -> str:
    return f"https://{settings.domain}/api/channels/shopify/oauth/callback"


@router.get("/shopify/install")
def shopify_install(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: User = Depends(require_module("channel_orders")),
):
    """Start the Shopify OAuth install: redirect the merchant to Shopify's
    consent screen. The signed ``state`` carries the organization id."""
    if not (settings.shopify_api_key and settings.shopify_shop_domain and settings.domain):
        raise HTTPException(400, "Shopify is niet geconfigureerd (API key / shop / domein)")
    org_id = _resolve_org_id(user, organization_id)
    shop = settings.shopify_shop_domain
    if not is_valid_shop_domain(shop):
        raise HTTPException(400, f"Ongeldig shop-domein: {shop}")
    state = _state_signer.dumps({"org_id": org_id})
    return RedirectResponse(build_authorize_url(shop, _redirect_uri(), state))


@router.get("/shopify/oauth/callback")
def shopify_oauth_callback(request: Request, db: Session = Depends(get_db)):
    """Public OAuth callback. Verifies Shopify's HMAC and our signed state, then
    exchanges the code for an access token and stores it on the connection."""
    params = dict(request.query_params)
    shop = params.get("shop", "")
    code = params.get("code", "")

    if not verify_oauth_hmac(params):
        raise HTTPException(400, "Ongeldige HMAC")
    if not is_valid_shop_domain(shop):
        raise HTTPException(400, "Ongeldig shop-domein")
    if not code:
        raise HTTPException(400, "Geen autorisatiecode")
    try:
        data = _state_signer.loads(params.get("state", ""), max_age=_STATE_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(400, "State verlopen — start de koppeling opnieuw")
    except BadSignature:
        raise HTTPException(400, "Ongeldige state")
    org_id = data["org_id"]

    token_data = exchange_code_for_token(shop, code)

    connection = _get_or_create_connection(db, org_id, "shopify")
    connection.shop_domain = shop
    connection.access_token = token_data.get("access_token")
    connection.scope = token_data.get("scope")
    db.commit()

    # Back to the app; the reconciliation view (PR 4) shows the result.
    return RedirectResponse(f"https://{settings.domain}/?shopify=connected")


@router.post("/shopify/sync", response_model=ChannelSyncSummary)
def trigger_shopify_sync(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: User = Depends(require_module("channel_orders")),
):
    """Pull Shopify orders updated since the last sync and import them (observe)."""
    org_id = _resolve_org_id(user, organization_id)
    connection = _get_or_create_connection(db, org_id, "shopify")
    client = ShopifyClient(
        shop_domain=connection.shop_domain or settings.shopify_shop_domain,
        access_token=connection.access_token or settings.shopify_access_token,
    )
    if not client.configured:
        raise HTTPException(
            400, "Shopify is niet verbonden — koppel eerst via /channels/shopify/install"
        )
    summary = sync_shopify(db, connection, client)
    db.commit()
    return ChannelSyncSummary(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unmatched=summary.unmatched,
    )
