"""Channel integration endpoints (Fase 3) — platform-admin only.

Connecting a sales channel + running the observe-mode import is operator/setup
work, so every endpoint requires a platform admin and targets an explicit
organization (the data-bak the orders land in). The OAuth callback is the one
exception: it is public because Shopify redirects the browser to it without our
auth token, and is instead secured by Shopify's HMAC + a signed ``state``.
"""
import datetime
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_platform_admin
from app.config import settings
from app.database import get_db
from app.models import (
    Booking,
    CarrierConnection,
    ChannelConnection,
    ChannelSyncLog,
    Order,
    OrderLine,
    Organization,
    SKU,
    User,
)
from app.schemas import (
    CarrierStatus,
    ChannelConnectUrl,
    ChannelModeRequest,
    ChannelOrderRow,
    ChannelReconciliation,
    ChannelReviewResolveRequest,
    ChannelReviewResolveResponse,
    ChannelStatus,
    ChannelSyncSummary,
    InventoryPushSummary,
    VeloydConnectRequest,
    VeloydWebhookUrl,
)
from app.services.channel_import import CANCELLATION_REVIEW_REASONS
from app.services.bol import (
    BolAPIError,
    BolAuthenticationError,
    BolClient,
    BolConfigurationError,
    clear_token_cache,
    sync_bol,
)
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_access_token,
    has_access_token,
    store_access_token,
    store_carrier_api_key,
    has_carrier_api_key,
)
from app.services.veloyd import (
    VELOYD_CARRIER,
    VeloydClient,
    VeloydError,
    VeloydNotConnected,
    client_for_organization,
)
from app.services.veloyd_webhook import issue_webhook_token
from app.services.inventory_sync import (
    push_available,
    push_bol_available,
    push_inventory_to_channels,
)
from app.services.advice_channel import (
    advice_connection,
    resolve_advice_organization,
)
from app.services.stock import adjust_reservation, apply_stock_movement
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
        connected=has_access_token(conn),
        shop_domain=conn.shop_domain,
        mode=conn.mode,
        last_synced_at=conn.last_synced_at,
    )


def _bol_status_for(conn: ChannelConnection | None) -> ChannelStatus:
    configured = bool(
        settings.bol_client_id
        and settings.bol_client_secret
        and settings.bol_token_url
        and settings.bol_api_base_url
    )
    return ChannelStatus(
        connected=bool(conn and conn.status == "active" and configured),
        mode=conn.mode if conn else None,
        last_synced_at=conn.last_synced_at if conn else None,
    )


def _raise_bol_http(exc: Exception) -> None:
    if isinstance(exc, BolConfigurationError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, BolAuthenticationError):
        # Do not return HTTP 401: that status means the Admin's own login expired
        # to the SPA and would incorrectly sign the operator out.
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(503, str(exc)) from exc


def _reconciliation_for(
    db: Session, org_id: int, channel: str, status: ChannelStatus
) -> ChannelReconciliation:
    logs = (
        db.query(ChannelSyncLog)
        .filter(
            ChannelSyncLog.organization_id == org_id,
            ChannelSyncLog.channel == channel,
        )
        .order_by(ChannelSyncLog.synced_at.desc())
        .all()
    )
    orders_by_ext = {
        o.external_id: o
        for o in db.query(Order).filter(
            Order.organization_id == org_id, Order.channel == channel
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
                order_id=order.id if order else None,
                external_id=log.external_id,
                reference=order.reference if order else None,
                channel_reference=order.channel_reference if order else None,
                channel_fulfillment_status=(
                    order.channel_fulfillment_status if order else None
                ),
                review_reason=order.review_reason if order else None,
                ordered_at=order.ordered_at if order else None,
                channel_shipped_at=(order.channel_shipped_at if order else None),
                status=order.status if order else None,
                matched_lines=log.matched_lines,
                unmatched_eans=unmatched,
            )
        )
    return ChannelReconciliation(
        status=status,
        orders=rows,
        unmatched_eans=sorted(all_unmatched),
    )


def _sync_connection(
    db: Session,
    connection: ChannelConnection,
    background_tasks: BackgroundTasks,
    org_id: int,
) -> "SyncSummary":
    """Pull + import for one connection, commit, and schedule the reserved-SKU
    pushes to every live channel. Shared by manual sync and the go-live cutover.

    Per-connection credentials only — no global env fallback (cross-tenant).
    """
    try:
        access_token = get_access_token(connection)
    except CredentialEncryptionError as exc:
        logger.exception(
            "Shopify credential decryptie faalde voor connection %s", connection.id
        )
        raise HTTPException(
            503, "Shopify-credential kan niet veilig worden ontsleuteld"
        ) from exc
    client = ShopifyClient(
        shop_domain=connection.shop_domain,
        access_token=access_token,
    )
    if not client.configured:
        raise HTTPException(
            400, "Shopify is niet verbonden — koppel eerst via de Verbind-knop"
        )
    summary = sync_shopify(db, connection, client)
    db.commit()
    # Mirror newly-reserved SKUs to every live channel after the response.
    for sku_id in summary.reserved_sku_ids:
        background_tasks.add_task(push_inventory_to_channels, sku_id, org_id)
    return summary


def _sync_bol_connection(
    db: Session,
    connection: ChannelConnection,
    background_tasks: BackgroundTasks,
    org_id: int,
):
    """Pull bol orders, commit, then mirror affected stock cross-channel."""
    summary = sync_bol(db, connection, BolClient())
    db.commit()
    for sku_id in summary.reserved_sku_ids:
        background_tasks.add_task(push_inventory_to_channels, sku_id, org_id)
    return summary


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
    shop_changed = bool(connection.shop_domain) and connection.shop_domain != shop
    connection.shop_domain = shop
    token = token_data.get("access_token")
    if not token:
        db.rollback()
        raise HTTPException(502, "Shopify gaf geen access token terug")
    try:
        store_access_token(connection, token)
    except CredentialEncryptionError as exc:
        db.rollback()
        logger.exception(
            "Shopify credential encryptie faalde voor connection %s", connection.id
        )
        raise HTTPException(
            503, "Shopify-token kan niet veilig worden opgeslagen"
        ) from exc
    connection.scope = token_data.get("scope")
    if shop_changed:
        # A different shop invalidates every cached Shopify GID: the connection's
        # location_id and each SKU's inventory_item_id belonged to the old shop,
        # so keeping them would write stock to the wrong items.
        connection.shopify_location_id = None
        db.query(SKU).filter(SKU.organization_id == org_id).update(
            {SKU.shopify_inventory_item_id: None}, synchronize_session=False
        )
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
    background_tasks: BackgroundTasks,
    organization_id: int | None = Query(None),
    full: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Pull Shopify orders and import them (observe).

    Normally incremental: only orders updated since the connection cursor. With
    ``full=true`` the cursor is reset first, so Shopify re-sends the whole
    history — used once to backfill fields on orders imported by older code. The
    importer is idempotent, so this never duplicates orders.
    """
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    connection = _get_or_create_connection(db, org_id, "shopify")
    if full:
        connection.cursor = None
    summary = _sync_connection(db, connection, background_tasks, org_id)
    return ChannelSyncSummary(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unmatched=summary.unmatched,
    )


@router.get("/bol/status", response_model=ChannelStatus)
def bol_status(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    return _bol_status_for(_get_connection(db, org_id, "bol"))


@router.post("/bol/connect", response_model=ChannelStatus)
def connect_bol(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Validate the single server-side bol account and bind it to one org."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    bound_elsewhere = (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.channel == "bol",
            ChannelConnection.organization_id != org_id,
            ChannelConnection.status == "active",
        )
        .first()
    )
    if bound_elsewhere:
        raise HTTPException(
            409,
            "Het bol-account uit .env is al aan een andere organisatie gekoppeld",
        )

    client = BolClient()
    try:
        clear_token_cache()
        client.validate_credentials()
    except (BolConfigurationError, BolAuthenticationError, BolAPIError) as exc:
        db.rollback()
        _raise_bol_http(exc)

    connection = _get_or_create_connection(db, org_id, "bol")
    connection.status = "active"
    # Credentials may now point at a recreated or different bol account. Drop
    # cached offer ids for this org; the next write-back resolves them lazily.
    db.query(SKU).filter(SKU.organization_id == org_id).update(
        {SKU.bol_offer_id: None}, synchronize_session=False
    )
    db.commit()
    return _bol_status_for(connection)


@router.post("/bol/sync", response_model=ChannelSyncSummary)
def trigger_bol_sync(
    background_tasks: BackgroundTasks,
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Read FBR orders; live mode also mirrors affected available stock."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    connection = _get_connection(db, org_id, "bol")
    if not connection or not _bol_status_for(connection).connected:
        raise HTTPException(400, "bol is niet verbonden — koppel eerst in Admin")
    try:
        summary = _sync_bol_connection(
            db, connection, background_tasks, org_id
        )
    except (BolConfigurationError, BolAuthenticationError, BolAPIError) as exc:
        db.rollback()
        _raise_bol_http(exc)
    return ChannelSyncSummary(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unmatched=summary.unmatched,
    )


@router.post("/bol/mode", response_model=ChannelStatus)
def set_bol_mode(
    body: ChannelModeRequest,
    background_tasks: BackgroundTasks,
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Switch bol between safe observe mode and live picking/stock mode."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    mode = body.mode.strip().lower()
    if mode not in ("observe", "live"):
        raise HTTPException(400, "Ongeldige modus — kies 'observe' of 'live'")

    connection = _get_connection(db, org_id, "bol")
    if not connection or not _bol_status_for(connection).connected:
        raise HTTPException(400, "bol is niet verbonden — koppel eerst in Admin")

    if mode == "live":
        if (
            connection.mode != "live"
            or connection.inventory_authority_started_at is None
        ):
            connection.inventory_authority_started_at = datetime.datetime.utcnow()
        connection.mode = "live"
        # Open orders are fetched on every bol sync and will promote to active.
        # Keep the shipment cursor: observe already imported the history, so
        # resetting it would synchronously re-fetch up to three months here.
        try:
            _sync_bol_connection(db, connection, background_tasks, org_id)
        except (BolConfigurationError, BolAuthenticationError, BolAPIError) as exc:
            db.rollback()
            _raise_bol_http(exc)
    else:
        connection.mode = "observe"
        db.commit()
    return _bol_status_for(connection)


@router.post("/bol/push-inventory", response_model=InventoryPushSummary)
def push_bol_inventory(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Align every local EAN product with its single matching bol FBR offer."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    connection = _get_connection(db, org_id, "bol")
    if not connection or not _bol_status_for(connection).connected:
        raise HTTPException(400, "bol is niet verbonden — koppel eerst in Admin")
    if connection.mode != "live":
        raise HTTPException(
            400, "Verbinding staat op observe — zet live voordat je voorraad pusht"
        )

    skus = (
        db.query(SKU)
        .filter(SKU.organization_id == org_id, SKU.ean.isnot(None))
        .all()
    )
    pushed = skipped = failed = 0
    for sku in skus:
        try:
            if push_bol_available(db, sku.id, org_id):
                pushed += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
            logger.exception("bol bulk push failed for sku %s (org %s)", sku.id, org_id)
    db.commit()
    return InventoryPushSummary(
        total=len(skus), pushed=pushed, skipped_no_variant=skipped, failed=failed
    )


@router.post("/shopify/mode", response_model=ChannelStatus)
def set_shopify_mode(
    body: ChannelModeRequest,
    background_tasks: BackgroundTasks,
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Flip a connection between observe and live — the go-live cutover.

    Going live is the operator's explicit "start shipping" switch: from now on
    new paid orders become pickable and stock is written back to Shopify. To
    bring the orders already imported in observe-mode into the pick list, the
    cursor is reset and a full re-sync runs — the importer promotes each observed
    order to ``active`` (and reserves its stock) now that the connection is live.

    Going back to observe only stops *future* activations; it does not retract
    orders that are already active (that downgrade path is deliberately separate).
    """
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    mode = body.mode.strip().lower()
    if mode not in ("observe", "live"):
        raise HTTPException(400, "Ongeldige modus — kies 'observe' of 'live'")

    connection = _get_or_create_connection(db, org_id, "shopify")
    if mode == "live":
        if not connection.shop_domain or not has_access_token(connection):
            raise HTTPException(
                400, "Shopify is niet verbonden — koppel eerst via de Verbind-knop"
            )
        if (
            connection.mode != "live"
            or connection.inventory_authority_started_at is None
        ):
            connection.inventory_authority_started_at = datetime.datetime.utcnow()
        connection.mode = "live"
        # Re-see the whole history so observed orders promote to active + reserve.
        connection.cursor = None
        _sync_connection(db, connection, background_tasks, org_id)
    else:
        connection.mode = "observe"
        db.commit()
    return _status_for(connection)


@router.post("/shopify/push-inventory", response_model=InventoryPushSummary)
def push_inventory(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """One-shot bulk push of the org's local stock to Shopify (start-alignment).

    The write-back is event-driven (a SKU mirrors when it is picked/adjusted), so
    a freshly (re)connected shop only catches up SKU-by-SKU. This pushes the
    absolute available for every EAN product at once, so Shopify matches the
    warehouse immediately after going live. Admin-only, like the order sync.

    Synchronous with a per-SKU summary: a failed push is counted and the run
    continues — one bad SKU never aborts the alignment.
    """
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    connection = _get_or_create_connection(db, org_id, "shopify")
    if not connection.shop_domain or not has_access_token(connection):
        raise HTTPException(
            400, "Shopify is niet verbonden — koppel eerst via de Verbind-knop"
        )
    if connection.mode != "live":
        raise HTTPException(
            400, "Verbinding staat op observe — zet live voordat je voorraad pusht"
        )

    skus = (
        db.query(SKU)
        .filter(SKU.organization_id == org_id, SKU.ean.isnot(None))
        .all()
    )
    pushed = skipped = failed = 0
    for sku in skus:
        try:
            if push_available(db, sku.id, org_id):
                pushed += 1
            else:
                skipped += 1
        except Exception:  # one bad SKU must not abort the alignment
            failed += 1
            logger.exception("Bulk push failed for sku %s (org %s)", sku.id, org_id)
    db.commit()
    return InventoryPushSummary(
        total=len(skus), pushed=pushed, skipped_no_variant=skipped, failed=failed
    )


@router.post(
    "/shopify/orders/{order_id}/resolve",
    response_model=ChannelReviewResolveResponse,
)
def resolve_review_order(
    order_id: int,
    body: ChannelReviewResolveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Cancel a source-cancelled order after picking already started.

    The operator explicitly chooses whether physically picked units return to
    stock. Generic resume is deliberately unavailable: the source-side cause is
    still present and the next sync would simply park the order again.
    """
    order = db.get(Order, order_id)
    if order is None or order.channel == "manual":
        raise HTTPException(404, "Kanaalorder niet gevonden")

    # Lock bookings first (same order as undo_booking), then lines and the order.
    # This serializes against both undo and a concurrent picker before changing
    # stock, reservations and lifecycle in one commit.
    bookings = (
        db.query(Booking)
        .filter(Booking.order_id == order_id)
        .with_for_update()
        .all()
    )
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order_id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    db.refresh(order, with_for_update=True)
    if order.status != "needs_review":
        raise HTTPException(400, "Order staat niet op handmatige controle")
    if order.review_reason not in CANCELLATION_REVIEW_REASONS:
        raise HTTPException(
            400,
            "Deze controle kan niet met een annuleringsactie worden afgehandeld",
        )
    if order.organization_id is None:
        raise HTTPException(409, "Kanaalorder heeft geen organisatie")

    open_by_sku: dict[int, int] = {}
    booked_by_sku: dict[int, int] = {}
    for line in lines:
        open_by_sku[line.sku_id] = open_by_sku.get(line.sku_id, 0) + max(
            0, line.quantity - line.booked_count
        )
        booked_by_sku[line.sku_id] = booked_by_sku.get(line.sku_id, 0) + max(
            0, line.booked_count
        )

    affected: set[int] = set()
    # Stable product order prevents two multi-product cancellations from locking
    # inventory balances in opposite order.
    for sku_id, open_qty in sorted(open_by_sku.items()):
        if open_qty:
            adjust_reservation(
                db,
                sku_id=sku_id,
                organization_id=order.organization_id,
                delta=-open_qty,
            )
            affected.add(sku_id)

    if body.action == "cancel_restock":
        for sku_id, booked_qty in sorted(booked_by_sku.items()):
            if booked_qty:
                apply_stock_movement(
                    db,
                    sku_id=sku_id,
                    organization_id=order.organization_id,
                    quantity=booked_qty,
                    movement_type="pick",
                    reference_type="channel_cancel",
                    reference_id=order.id,
                    note="Teruggeboekt na annulering van kanaalorder",
                    performed_by=user.id,
                )
                affected.add(sku_id)
        for line in lines:
            line.booked_count = 0
        for booking in bookings:
            db.delete(booking)

    order.status = "cancelled"
    order.review_reason = None
    db.commit()
    for sku_id in sorted(affected):
        background_tasks.add_task(
            push_inventory_to_channels, sku_id, order.organization_id
        )

    return ChannelReviewResolveResponse(status=order.status)


@router.get("/advice/status", response_model=ChannelStatus)
def advice_status(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Whether the wijnadvies connection is observing or live.

    Readable by the merchant, not just a platform admin: it decides whether
    their delivery orders turn into warehouse work, so it belongs on a screen
    they can open themselves.
    """
    org_id = resolve_advice_organization(db, user, organization_id)
    connection = advice_connection(db, org_id)
    db.commit()
    return ChannelStatus(
        connected=bool(settings.advice_sales_api_key),
        mode=connection.mode,
        last_synced_at=connection.last_synced_at,
    )


@router.post("/advice/mode", response_model=ChannelStatus)
def set_advice_mode(
    body: ChannelModeRequest,
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Flip the wijnadvies connection between observing and live.

    Live means a delivery order that arrives from now on is born pickable, and
    its hold is settled by the pick that empties the shelf instead of by the
    advice app's own collect. Orders that were already observed stay observed —
    see ``advice_is_live`` for why activating them afterwards is unsafe.

    Going back to observe stops future orders from becoming work; it does not
    retract orders that are already active. Those are finished or cancelled
    through the order itself, the same way every other channel handles it.
    """
    org_id = resolve_advice_organization(db, user, organization_id)
    mode = body.mode.strip().lower()
    if mode not in ("observe", "live"):
        raise HTTPException(400, "Ongeldige modus — kies 'observe' of 'live'")

    if mode == "live" and not settings.advice_sales_api_key:
        # Without the write key the advice app cannot post an order at all, so
        # going live would only produce a switch that never does anything.
        raise HTTPException(
            400, "De wijnadvies-koppeling is niet geconfigureerd op deze server"
        )

    if mode == "live":
        # A live order is registered at the carrier the moment it arrives, and
        # that may never happen under another merchant's account. Refusing here
        # is the difference between a switch that cannot work and a stream of
        # orders whose boxes silently fail to be announced.
        try:
            client_for_organization(db, org_id, allow_legacy_fallback=False)
        except VeloydNotConnected as exc:
            raise HTTPException(
                409,
                "Koppel eerst het eigen Veloyd-account van deze organisatie",
            ) from exc

    connection = advice_connection(db, org_id)
    connection.mode = mode
    db.commit()
    return ChannelStatus(
        connected=bool(settings.advice_sales_api_key),
        mode=connection.mode,
        last_synced_at=connection.last_synced_at,
    )


@router.get("/shopify/reconciliation", response_model=ChannelReconciliation)
def shopify_reconciliation(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Observe-mode overview: per imported order how many lines matched a SKU and
    which EANs did not, plus the deduped list of all unmatched EANs to fix."""
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    return _reconciliation_for(
        db,
        org_id,
        "shopify",
        _status_for(_get_connection(db, org_id, "shopify")),
    )


@router.get("/bol/reconciliation", response_model=ChannelReconciliation)
def bol_reconciliation(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    org_id = _require_org_id(organization_id)
    _assert_org_has_channel(db, org_id)
    return _reconciliation_for(
        db,
        org_id,
        "bol",
        _bol_status_for(_get_connection(db, org_id, "bol")),
    )


def _carrier_status_for(conn: CarrierConnection | None) -> CarrierStatus:
    return CarrierStatus(
        carrier=VELOYD_CARRIER,
        connected=has_carrier_api_key(conn),
        base_url=conn.base_url if conn else None,
        updated_at=conn.updated_at if conn else None,
    )


@router.get("/veloyd/status", response_model=CarrierStatus)
def veloyd_status(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    org_id = _require_org_id(organization_id)
    connection = (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.organization_id == org_id,
            CarrierConnection.carrier == VELOYD_CARRIER,
        )
        .first()
    )
    return _carrier_status_for(connection)


@router.post("/veloyd/connect", response_model=CarrierStatus)
def connect_veloyd(
    body: VeloydConnectRequest,
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Bind one merchant's carrier account to its organization.

    The key is checked against Veloyd before it is stored, so a typo surfaces
    here instead of at the shipping-label gate with a packed box on the table.

    Deliberately not gated on the ``channel_orders`` module like the sales
    channels are: a carrier is not a channel. The merchant that ships the
    advice app's delivery orders picks on images and has no channel at all, and
    it still needs an account to print labels from.
    """
    org_id = _require_org_id(organization_id)
    if db.get(Organization, org_id) is None:
        raise HTTPException(404, "Organisatie niet gevonden")

    api_key = body.api_key.strip()
    base_url = (body.base_url or "").strip() or None
    try:
        VeloydClient(api_key=api_key, base_url=base_url).validate_credentials()
    except VeloydError as exc:
        raise HTTPException(502, str(exc)) from exc

    connection = (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.organization_id == org_id,
            CarrierConnection.carrier == VELOYD_CARRIER,
        )
        .first()
    )
    if connection is None:
        connection = CarrierConnection(
            organization_id=org_id, carrier=VELOYD_CARRIER
        )
        db.add(connection)
        db.flush()
    connection.base_url = base_url
    try:
        store_carrier_api_key(connection, api_key)
    except CredentialEncryptionError as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc
    db.commit()
    return _carrier_status_for(connection)


@router.post("/veloyd/webhook-url", response_model=VeloydWebhookUrl)
def issue_veloyd_webhook_url(
    organization_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_platform_admin),
):
    """Mint the URL to paste into Veloyd's "Webhook URL zendingen" field.

    Returned once and never again: Veloyd offers no header or signature on that
    field, so the secret lives in the path and only its digest is stored here.
    Calling this a second time issues a new URL and retires the old one, which
    is also how a leaked URL is revoked.
    """
    org_id = _require_org_id(organization_id)
    if not settings.domain:
        raise HTTPException(
            503, "DOMAIN is niet geconfigureerd; webhook-URL kan niet worden gemaakt"
        )
    connection = (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.organization_id == org_id,
            CarrierConnection.carrier == VELOYD_CARRIER,
        )
        .first()
    )
    if connection is None:
        raise HTTPException(409, "Koppel eerst het Veloyd-account van deze organisatie")

    token = issue_webhook_token(connection)
    db.commit()
    return VeloydWebhookUrl(
        url=f"https://{settings.domain}/api/integrations/veloyd/webhook/{token}"
    )
