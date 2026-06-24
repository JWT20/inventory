"""Channel integration endpoints (Fase 3) — platform-admin only.

Connecting a sales channel + running the observe-mode import is operator/setup
work, so every endpoint requires a platform admin and targets an explicit
organization (the data-bak the orders land in). The OAuth callback is the one
exception: it is public because Shopify redirects the browser to it without our
auth token, and is instead secured by Shopify's HMAC + a signed ``state``.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import require_platform_admin
from app.config import settings
from app.database import get_db
from app.models import ChannelConnection, Order, Organization, User
from app.schemas import (
    ChannelConnectUrl,
    ChannelOrderRow,
    ChannelReconciliation,
    ChannelStatus,
    ChannelSyncSummary,
)
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


def _require_org_id(requested: int | None) -> int:
    if not requested:
        raise HTTPException(400, "organization_id is verplicht")
    return requested


def _assert_org_has_channel(db: Session, org_id: int) -> None:
    """The target org must actually have the channel_orders module — admins can
    target any org, so this is the real gate, not the caller's role."""
    org = db.get(Organization, org_id)
    if not org or "channel_orders" not in org.modules:
        raise HTTPException(403, "Organisatie heeft de kanaal-module niet")


def _get_connection(db: Session, org_id: int, channel: str) -> ChannelConnection | None:
    return (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == org_id,
            ChannelConnection.channel == channel,
        )
        .first()
    )


def _get_or_create_connection(db: Session, org_id: int, channel: str) -> ChannelConnection:
    conn = _get_connection(db, org_id, channel)
    if conn is None:
        # New connections start in observe-mode — never act by surprise.
        conn = ChannelConnection(organization_id=org_id, channel=channel, mode="observe")
        db.add(conn)
        db.flush()
    return conn


def _redirect_uri() -> str:
    return f"https://{settings.domain}/api/channels/shopify/oauth/callback"


def _build_authorize_url(db: Session, org_id: int) -> str:
    """Config-check + build the Shopify OAuth authorize URL for an org."""
    if not (settings.shopify_api_key and settings.shopify_shop_domain and settings.domain):
        raise HTTPException(400, "Shopify is niet geconfigureerd (API key / shop / domein)")
    _assert_org_has_channel(db, org_id)
    shop = settings.shopify_shop_domain
    if not is_valid_shop_domain(shop):
        raise HTTPException(400, f"Ongeldig shop-domein: {shop}")
    # state carries org + intended shop so the callback can reject a token bound
    # to a different (but validly signed) shop.
    state = _state_signer.dumps({"org_id": org_id, "shop": shop})
    return build_authorize_url(shop, _redirect_uri(), state)


def _status_for(conn: ChannelConnection | None) -> ChannelStatus:
    if conn is None:
        return ChannelStatus(connected=False)
    return ChannelStatus(
        connected=bool(conn.access_token),
        shop_domain=conn.shop_domain,
        mode=conn.mode,
        last_synced_at=conn.last_synced_at,
    )


@router.get("/shopify/connect-url", response_model=ChannelConnectUrl)
def shopify_connect_url(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Return the Shopify OAuth URL for the SPA to navigate to (the browser
    cannot carry our auth header on a top-level redirect, so the SPA fetches the
    URL with auth and then sets window.location)."""
    org_id = _require_org_id(organization_id)
    return ChannelConnectUrl(url=_build_authorize_url(db, org_id))


@router.get("/shopify/install")
def shopify_install(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Redirect variant of connect-url (e.g. for curl with an admin token)."""
    org_id = _require_org_id(organization_id)
    return RedirectResponse(_build_authorize_url(db, org_id))


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
    # Shopify signs a valid callback for ANY *.myshopify.com — bind it to the
    # shop the install was started for.
    if shop != data.get("shop"):
        raise HTTPException(400, "Shop komt niet overeen met de installatie")

    token_data = exchange_code_for_token(shop, code)

    connection = _get_or_create_connection(db, org_id, "shopify")
    connection.shop_domain = shop
    connection.access_token = token_data.get("access_token")
    connection.scope = token_data.get("scope")
    db.commit()

    return RedirectResponse(f"https://{settings.domain}/?shopify=connected")


@router.get("/shopify/status", response_model=ChannelStatus)
def shopify_status(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    return _status_for(_get_connection(db, org_id, "shopify"))


@router.post("/shopify/sync", response_model=ChannelSyncSummary)
def trigger_shopify_sync(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Pull Shopify orders updated since the last sync and import them (observe)."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    connection = _get_or_create_connection(db, org_id, "shopify")
    # Per-connection credentials only — no global env fallback (cross-tenant).
    client = ShopifyClient(
        shop_domain=connection.shop_domain,
        access_token=connection.access_token,
    )
    if not client.configured:
        raise HTTPException(
            400, "Shopify is niet verbonden — koppel eerst via de Verbind-knop"
        )
    summary = sync_shopify(db, connection, client)
    db.commit()
    return ChannelSyncSummary(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unmatched=summary.unmatched,
    )


@router.get("/shopify/reconciliation", response_model=ChannelReconciliation)
def shopify_reconciliation(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Observe-mode overview: per imported order how many lines matched a SKU and
    which EANs did not, plus the deduped list of all unmatched EANs to fix."""
    from app.models import ChannelSyncLog

    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)

    logs = (
        db.query(ChannelSyncLog)
        .filter(
            ChannelSyncLog.organization_id == org_id,
            ChannelSyncLog.channel == "shopify",
        )
        .order_by(ChannelSyncLog.synced_at.desc())
        .all()
    )
    orders_by_ext = {
        o.external_id: o
        for o in db.query(Order).filter(
            Order.organization_id == org_id, Order.channel == "shopify"
        )
    }

    rows: list[ChannelOrderRow] = []
    all_unmatched: set[str] = set()
    for log in logs:
        unmatched = json.loads(log.unmatched_eans or "[]")
        all_unmatched.update(unmatched)
        order = orders_by_ext.get(log.external_id)
        rows.append(
            ChannelOrderRow(
                external_id=log.external_id,
                reference=order.reference if order else None,
                channel_reference=order.channel_reference if order else None,
                ordered_at=order.ordered_at if order else None,
                status=order.status if order else None,
                matched_lines=log.matched_lines,
                unmatched_eans=unmatched,
            )
        )

    return ChannelReconciliation(
        status=_status_for(_get_connection(db, org_id, "shopify")),
        orders=rows,
        unmatched_eans=sorted(all_unmatched),
    )
