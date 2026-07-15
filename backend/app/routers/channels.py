"""Channel integration endpoints (Fase 3) — platform-admin only.

Connecting a sales channel + running the observe-mode import is operator/setup
work, so every endpoint requires a platform admin and targets an explicit
organization (the data-bak the orders land in). The OAuth callback is the one
exception: it is public because Shopify redirects the browser to it without our
auth token, and is instead secured by Shopify's HMAC + a signed ``state``.
"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import require_platform_admin
from app.config import settings
from app.database import get_db
from app.models import (
    Booking,
    ChannelConnection,
    Order,
    OrderLine,
    Organization,
    SKU,
    User,
)
from app.schemas import (
    ChannelConnectUrl,
    ChannelModeRequest,
    ChannelOrderRow,
    ChannelReconciliation,
    ChannelReviewResolveRequest,
    ChannelReviewResolveResponse,
    ChannelStatus,
    ChannelSyncSummary,
    InventoryPushSummary,
)
from app.services.channel_import import CANCELLATION_REVIEW_REASONS
from app.services.inventory_sync import push_available, push_inventory_to_shopify
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
        connected=bool(conn.access_token),
        shop_domain=conn.shop_domain,
        mode=conn.mode,
        last_synced_at=conn.last_synced_at,
    )


def _sync_connection(
    db: Session,
    connection: ChannelConnection,
    background_tasks: BackgroundTasks,
    org_id: int,
) -> "SyncSummary":
    """Pull + import for one connection, commit, and schedule the reserved-SKU
    pushes to Shopify. Shared by the manual sync and the go-live cutover.

    Per-connection credentials only — no global env fallback (cross-tenant).
    """
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
    # Mirror newly-reserved SKUs to Shopify after the response (best-effort), so
    # going live immediately drops the storefront available by the open orders.
    for sku_id in summary.reserved_sku_ids:
        background_tasks.add_task(push_inventory_to_shopify, sku_id, org_id)
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
    connection.access_token = token_data.get("access_token")
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
        if not connection.shop_domain or not connection.access_token:
            raise HTTPException(
                400, "Shopify is niet verbonden — koppel eerst via de Verbind-knop"
            )
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
    if not connection.shop_domain or not connection.access_token:
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
            push_inventory_to_shopify, sku_id, order.organization_id
        )

    return ChannelReviewResolveResponse(status=order.status)


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
                order_id=order.id if order else None,
                external_id=log.external_id,
                reference=order.reference if order else None,
                channel_reference=order.channel_reference if order else None,
                channel_fulfillment_status=(
                    order.channel_fulfillment_status if order else None
                ),
                review_reason=order.review_reason if order else None,
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
