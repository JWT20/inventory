"""Server-to-server integration endpoints."""

import datetime
import json
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    SELLABLE_INVENTORY_LOCATIONS,
    AdviceReservation,
    AdviceReservationLine,
    AdviceSale,
    AdviceSaleLine,
    ChannelSyncLog,
    InventoryBalance,
    Order,
    OrderDeliveryAddress,
    OrderLine,
    Organization,
    SKU,
)
from app.schemas import (
    AdviceOrderMatchedLine,
    AdviceStockPool,
    AdviceOrderRequest,
    AdviceOrderResponse,
    AdviceReservationLineResponse,
    AdviceReservationRequest,
    AdviceReservationResponse,
    AdviceSaleAppliedLine,
    AdviceSaleRequest,
    AdviceSaleResponse,
    AdviceStockItem,
    AdviceStockResponse,
    DeliveryAddressIn,
)
from app.services.advice_channel import (
    ADVICE_CHANNEL,
    AdviceChannelNotObserving,
    advice_connection,
    assert_advice_observing,
)
from app.services.stock import (
    adjust_reservation,
    apply_stock_movement,
    lock_ordered,
)

router = APIRouter(prefix="/integrations/advice", tags=["integrations"])


def _authenticate(expected_key: str, authorization: str | None) -> int:
    """Shared bearer check for the advice integration endpoints.

    Each direction has its own key but the same bound organization: callers can
    never select a different merchant.
    """
    organization_id = settings.advice_stock_organization_id
    if not expected_key or organization_id is None:
        raise HTTPException(503, "Advice stock integration is not configured")

    parts = (authorization or "").split(maxsplit=1)
    provided_key = (
        parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    )
    provided_key = provided_key.encode("utf-8")
    if not secrets.compare_digest(provided_key, expected_key.encode("utf-8")):
        raise HTTPException(401, "Invalid inventory key")

    return organization_id


def _authenticate_advice_stock_request(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> int:
    return _authenticate(settings.advice_stock_api_key, authorization)


def _authenticate_advice_sales_request(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> int:
    return _authenticate(settings.advice_sales_api_key, authorization)


@router.get("/stock", response_model=AdviceStockResponse)
def advice_stock(
    response: Response,
    inventory_location: AdviceStockPool = "store",
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_stock_request),
) -> AdviceStockResponse:
    """Return available bottle stock for the configured organization.

    The shop and the webshop are two physical places but one sellable pool, so
    ``sellable`` reports them added together — that is what the webshop can
    actually sell. Every response also carries the split, so the shop can say
    where a bottle is standing without a second call.

    ``store``, ``webshop`` and ``warehouse`` keep working and keep meaning one
    pool; the feed is live, so the caller changes when it is ready.
    """
    if db.get(Organization, organization_id) is None:
        raise HTTPException(
            503,
            "Advice stock organization is not configured correctly",
        )

    rows = (
        db.query(
            SKU.id,
            SKU.source_product_id,
            SKU.sku_code,
            SKU.active,
            InventoryBalance.inventory_location,
            InventoryBalance.quantity_on_hand,
            InventoryBalance.quantity_reserved,
        )
        .outerjoin(
            InventoryBalance,
            and_(
                InventoryBalance.sku_id == SKU.id,
                InventoryBalance.organization_id == organization_id,
            ),
        )
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
        )
        .order_by(SKU.sku_code)
        .all()
    )

    # One row per (product, pool) comes back; fold them into one item per
    # product carrying every pool it has a balance in.
    available_by_sku: dict[int, dict[str, int]] = {}
    items: dict[int, AdviceStockItem] = {}
    for (
        sku_id,
        source_product_id,
        sku_code,
        active,
        location,
        quantity_on_hand,
        quantity_reserved,
    ) in rows:
        if sku_id not in items:
            items[sku_id] = AdviceStockItem(
                source_product_id=source_product_id,
                sku_code=sku_code,
                is_bottle=True,
                quantity_available=0,
            )
            available_by_sku[sku_id] = {}
        if location is None:
            continue
        # An inactive product is unsellable whatever the shelf holds, so it is
        # reported at zero rather than being left out — the advice app needs to
        # see it go to zero, not see it disappear.
        available_by_sku[sku_id][location] = (
            max((quantity_on_hand or 0) - (quantity_reserved or 0), 0)
            if active
            else 0
        )

    requested = (
        SELLABLE_INVENTORY_LOCATIONS
        if inventory_location == "sellable"
        else (inventory_location,)
    )
    for sku_id, item in items.items():
        per_pool = available_by_sku[sku_id]
        item.quantity_store = per_pool.get("store", 0)
        item.quantity_webshop = per_pool.get("webshop", 0)
        item.quantity_available = sum(per_pool.get(pool, 0) for pool in requested)

    response.headers["Cache-Control"] = "no-store"
    return AdviceStockResponse(items=list(items.values()))


@router.post("/sales", response_model=AdviceSaleResponse)
def advice_sale(
    payload: AdviceSaleRequest,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_sales_request),
) -> AdviceSaleResponse:
    """Book a completed counter sale off store stock.

    Deliberately fail-open: the shop counter reports a sale that already
    happened, so an unknown product or a short balance never rejects the whole
    report. Unknown products come back in ``unmatched`` for the operator to fix
    by linking the product and re-posting; stock is allowed to go negative so
    the discrepancy stays visible instead of silently blocking the till.
    """
    if db.get(Organization, organization_id) is None:
        raise HTTPException(
            503,
            "Advice stock organization is not configured correctly",
        )

    # One product may appear on several lines (two separate scans of the same
    # wine). Collapse them so the sale books one movement per product and the
    # (sale, sku) uniqueness that makes retries safe still holds.
    requested: dict[str, int] = {}
    product_order: list[str] = []
    for line in payload.lines:
        if line.source_product_id not in requested:
            product_order.append(line.source_product_id)
            requested[line.source_product_id] = 0
        requested[line.source_product_id] += line.quantity

    skus = {
        sku.source_product_id: sku
        for sku in db.query(SKU)
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
            SKU.source_product_id.in_(list(requested)),
        )
        .all()
    }

    def _load_sale() -> AdviceSale | None:
        return (
            db.query(AdviceSale)
            .filter(
                AdviceSale.organization_id == organization_id,
                AdviceSale.sale_id == payload.sale_id,
            )
            .with_for_update()
            .first()
        )

    sale = _load_sale()
    if sale is None:
        candidate = AdviceSale(
            organization_id=organization_id,
            sale_id=payload.sale_id,
            channel=payload.channel,
            occurred_at=payload.occurred_at,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            sale = candidate
        except IntegrityError:
            # Two retries of the same sale raced; the other one created it.
            # Rolling back the savepoint already makes the failed candidate
            # transient. Calling ``expunge`` here would itself raise because
            # the instance is no longer present in the session.
            sale = _load_sale()
            if sale is None:
                raise

    booked_sku_ids = {
        line.sku_id
        for line in db.query(AdviceSaleLine).filter(
            AdviceSaleLine.sale_id == sale.id
        )
    }

    applied: list[tuple[str, SKU, int]] = []
    duplicate: list[str] = []
    unmatched: list[str] = []
    for product_id in product_order:
        sku = skus.get(product_id)
        if sku is None:
            unmatched.append(product_id)
            continue
        if sku.id in booked_sku_ids:
            duplicate.append(product_id)
            continue
        quantity = requested[product_id]
        movement = apply_stock_movement(
            db,
            sku_id=sku.id,
            organization_id=organization_id,
            quantity=-quantity,
            movement_type="sale",
            reference_type="advice_sale",
            reference_id=sale.id,
            note=f"{payload.channel} {payload.sale_id}",
            performed_by=None,
            allow_negative=True,
            inventory_location="store",
        )
        db.add(
            AdviceSaleLine(
                sale_id=sale.id,
                sku_id=sku.id,
                quantity=quantity,
                stock_movement_id=movement.id,
            )
        )
        applied.append((product_id, sku, quantity))

    db.commit()

    balances = {
        balance.sku_id: balance.quantity_available
        for balance in db.query(InventoryBalance).filter(
            InventoryBalance.organization_id == organization_id,
            InventoryBalance.inventory_location == "store",
            InventoryBalance.sku_id.in_([sku.id for _, sku, _ in applied] or [0]),
        )
    }
    return AdviceSaleResponse(
        sale_id=payload.sale_id,
        applied=[
            AdviceSaleAppliedLine(
                source_product_id=product_id,
                sku_code=sku.sku_code,
                quantity=quantity,
                quantity_available=balances.get(sku.id, 0),
            )
            for product_id, sku, quantity in applied
        ],
        duplicate=duplicate,
        unmatched=unmatched,
    )


def _same_route(stored: str, requested: str) -> bool:
    """Whether a retry names the route the hold was actually taken on.

    A delivery used to be routed from the warehouse. Holds taken before that
    switch are still settled against the pool they really came out of, so their
    stored name stays "warehouse" — but a caller retrying such an order means
    the same route whichever of the two names it sends.
    """
    if stored == requested:
        return True
    return {stored, requested} == {"warehouse", "webshop"}


def _pool_preference(inventory_location: str) -> tuple[str, ...]:
    """Which pools this route draws on, best first.

    The shop and the webshop hold the same sellable stock in two places, so a
    route that runs out on its own shelf may take the rest from the other one
    rather than refusing an order the building can serve. The preferred shelf
    goes first so the route the other one depends on is not emptied for nothing.
    """
    others = tuple(
        pool for pool in SELLABLE_INVENTORY_LOCATIONS if pool != inventory_location
    )
    return (inventory_location, *others)


def _hold_across_pools(
    db: Session,
    *,
    sku_id: int,
    organization_id: int,
    pools: tuple[str, ...],
    quantity: int,
) -> list[tuple[str, int]]:
    """Reserve ``quantity`` bottles across ``pools``, filling each in turn.

    Returns what was taken per pool. Raises the same 409 the single-pool hold
    raised when the pools together fall short — a caller that cannot be served
    must hear that before a payment starts, not after.
    """
    # Take every row this hold could touch before deciding anything, in the one
    # order every multi-pool operation uses. Locking in preference order instead
    # would have a pickup grab the shop row first and a delivery the webshop row
    # first: two holds that both need to split can then each sit on one row
    # waiting for the other, and one customer gets a database error mid-checkout.
    # The route preference decides how the bottles are divided, never which row
    # is claimed first.
    balances = {
        balance.inventory_location: balance
        for balance in (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == sku_id,
                InventoryBalance.organization_id == organization_id,
                InventoryBalance.inventory_location.in_(pools),
            )
            .order_by(
                case(
                    {
                        pool: index
                        for index, pool in enumerate(lock_ordered(pools))
                    },
                    value=InventoryBalance.inventory_location,
                )
            )
            .with_for_update()
            .all()
        )
    }

    taken: list[tuple[str, int]] = []
    remaining = quantity
    for pool in pools:
        if remaining <= 0:
            break
        balance = balances.get(pool)
        available = balance.quantity_available if balance else 0
        if available <= 0:
            continue
        portion = min(available, remaining)
        adjust_reservation(
            db,
            sku_id=sku_id,
            organization_id=organization_id,
            inventory_location=pool,
            delta=portion,
            require_available=True,
        )
        taken.append((pool, portion))
        remaining -= portion
    if remaining > 0:
        # Say it outright rather than provoking the shared guard into saying it:
        # that call would only raise while the preferred pool is still empty, and
        # on the day it is not, it would quietly hold stock no line accounts for.
        # The number that matters here is what the two shelves hold together,
        # which is also the number the caller can do something about.
        sku = db.get(SKU, sku_id)
        sku_code = sku.sku_code if sku else str(sku_id)
        raise HTTPException(
            409,
            f"Onvoldoende voorraad voor {sku_code}: "
            f"{quantity - remaining} beschikbaar, {quantity} nodig",
        )
    return taken


def _reservation_response(
    reservation: AdviceReservation, *, duplicate: bool
) -> AdviceReservationResponse:
    # Echo the stored routing, never a literal: a reservation is settled against
    # the pool it was taken from, whatever the current default happens to be.
    return AdviceReservationResponse(
        external_order_id=reservation.external_order_id,
        order_reference=reservation.order_reference,
        fulfillment_method=reservation.fulfillment_method,
        inventory_location=reservation.inventory_location,
        status=reservation.status,
        duplicate=duplicate,
        # A hold split across the shop and the webshop is one line to the
        # caller: it ordered a number of bottles, not a shelf.
        lines=[
            AdviceReservationLineResponse(
                source_product_id=source_product_id,
                sku_code=sku_code,
                quantity=quantity,
            )
            for (sku_code, source_product_id), quantity in sorted(
                _reserved_totals(reservation).items()
            )
        ],
    )


def _reserved_totals(
    reservation: AdviceReservation,
) -> dict[tuple[str, str | None], int]:
    """Bottles held per product, adding up any pools the hold was split over."""
    totals: dict[tuple[str, str | None], int] = {}
    for line in reservation.lines:
        key = (line.sku.sku_code, line.sku.source_product_id)
        totals[key] = totals.get(key, 0) + line.quantity
    return totals


def _locked_reservation(
    db: Session, organization_id: int, external_order_id: str
) -> AdviceReservation | None:
    # Lock the bare reservation row only. Eager-loading the lines here would put
    # FOR UPDATE on the nullable side of an outer join, which PostgreSQL rejects
    # outright; the lines load lazily from the same open session afterwards.
    return (
        db.query(AdviceReservation)
        .filter(
            AdviceReservation.organization_id == organization_id,
            AdviceReservation.external_order_id == external_order_id,
        )
        .with_for_update()
        .first()
    )


@router.post("/reservations", response_model=AdviceReservationResponse)
def reserve_advice_pickup(
    payload: AdviceReservationRequest,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_sales_request),
) -> AdviceReservationResponse:
    """Hold bottles for one advice-app order before its online payment starts.

    The caller states the routing and it is stored on the reservation, so
    collect and release settle against that pool rather than today's default.
    """
    if db.get(Organization, organization_id) is None:
        raise HTTPException(503, "Advice stock organization is not configured correctly")

    requested: dict[str, int] = {}
    for line in payload.lines:
        requested[line.source_product_id] = (
            requested.get(line.source_product_id, 0) + line.quantity
        )

    existing = _locked_reservation(db, organization_id, payload.external_order_id)
    if existing:
        # Sum per product: one product may be held on two shelves at once, and
        # a retry that ordered four bottles must still look like four.
        existing_lines: dict[str | None, int] = {}
        for line in existing.lines:
            existing_lines[line.sku.source_product_id] = (
                existing_lines.get(line.sku.source_product_id, 0) + line.quantity
            )
        if existing_lines != requested:
            raise HTTPException(
                409,
                "Deze order-ID bestaat al met andere productregels",
            )
        if existing.fulfillment_method != payload.fulfillment_method or not (
            _same_route(existing.inventory_location, payload.inventory_location)
        ):
            raise HTTPException(
                409,
                "Deze order-ID bestaat al met een andere route",
            )
        # Only an active hold may answer a retry as a no-op. A collected or
        # released reservation holds nothing, so replying "duplicate" would let
        # the caller start a payment against stock nobody is keeping aside.
        if existing.status != "active":
            raise HTTPException(
                409,
                f"Deze reservering is al afgehandeld ({existing.status})",
            )
        return _reservation_response(existing, duplicate=True)

    skus = {
        sku.source_product_id: sku
        for sku in db.query(SKU)
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
            SKU.active.is_(True),
            SKU.source_product_id.in_(list(requested)),
        )
        .all()
    }
    missing = sorted(product_id for product_id in requested if product_id not in skus)
    if missing:
        raise HTTPException(
            409,
            {"code": "unmatched_products", "source_product_ids": missing},
        )

    reservation = AdviceReservation(
        organization_id=organization_id,
        external_order_id=payload.external_order_id,
        order_reference=payload.order_reference,
        fulfillment_method=payload.fulfillment_method,
        inventory_location=payload.inventory_location,
        status="active",
    )
    db.add(reservation)
    pools = _pool_preference(reservation.inventory_location)
    try:
        db.flush()
        for product_id in sorted(requested):
            sku = skus[product_id]
            quantity = requested[product_id]
            for pool, portion in _hold_across_pools(
                db,
                sku_id=sku.id,
                organization_id=organization_id,
                pools=pools,
                quantity=quantity,
            ):
                db.add(
                    AdviceReservationLine(
                        reservation_id=reservation.id,
                        sku_id=sku.id,
                        quantity=portion,
                        inventory_location=pool,
                    )
                )
        db.commit()
    except HTTPException:
        # A short balance must roll back both the reservation row and every
        # earlier line hold from this request; FastAPI's yielded session does
        # not implicitly end that transaction before the next request.
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing = _locked_reservation(db, organization_id, payload.external_order_id)
        if existing:
            return _reservation_response(existing, duplicate=True)
        raise

    reservation = _locked_reservation(db, organization_id, payload.external_order_id)
    if reservation is None:  # pragma: no cover - the committed row cannot disappear here
        raise RuntimeError("Reservering verdween na opslaan")
    return _reservation_response(reservation, duplicate=False)


@router.post(
    "/reservations/{external_order_id}/collect",
    response_model=AdviceReservationResponse,
)
def collect_advice_pickup(
    external_order_id: str,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_sales_request),
) -> AdviceReservationResponse:
    """Consume one reservation when the customer receives the bottles."""
    reservation = _locked_reservation(db, organization_id, external_order_id)
    if not reservation:
        raise HTTPException(404, "Reservering niet gevonden")
    if reservation.status == "collected":
        return _reservation_response(reservation, duplicate=True)
    if reservation.status == "released":
        raise HTTPException(409, "Deze reservering is al vrijgegeven")

    for line in reservation.lines:
        # Each line names the shelf its bottles came off, so a hold that was
        # split across the shop and the webshop is settled on both.
        adjust_reservation(
            db,
            sku_id=line.sku_id,
            organization_id=organization_id,
            inventory_location=line.inventory_location,
            delta=-line.quantity,
        )
        apply_stock_movement(
            db,
            sku_id=line.sku_id,
            organization_id=organization_id,
            inventory_location=line.inventory_location,
            quantity=-line.quantity,
            movement_type="sale",
            reference_type="advice_pickup",
            reference_id=reservation.id,
            note=f"{reservation.fulfillment_method} {external_order_id}",
            performed_by=None,
        )
    reservation.status = "collected"
    reservation.collected_at = func.now()
    db.commit()
    return _reservation_response(reservation, duplicate=False)


@router.post(
    "/reservations/{external_order_id}/release",
    response_model=AdviceReservationResponse,
)
def release_advice_pickup(
    external_order_id: str,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_sales_request),
) -> AdviceReservationResponse:
    """Release held stock for a cancelled/refunded advice-app order."""
    reservation = _locked_reservation(db, organization_id, external_order_id)
    if not reservation:
        raise HTTPException(404, "Reservering niet gevonden")
    if reservation.status in {"released", "collected"}:
        return _reservation_response(reservation, duplicate=True)

    for line in reservation.lines:
        adjust_reservation(
            db,
            sku_id=line.sku_id,
            organization_id=organization_id,
            inventory_location=line.inventory_location,
            delta=-line.quantity,
        )
    reservation.status = "released"
    reservation.released_at = func.now()
    db.commit()
    return _reservation_response(reservation, duplicate=False)


def _write_delivery_address(
    order: Order, address: DeliveryAddressIn
) -> OrderDeliveryAddress:
    """Attach or refresh the shipping address of a delivery order."""
    stored = order.delivery_address or OrderDeliveryAddress(order_id=order.id)
    stored.recipient_name = address.recipient_name.strip()
    stored.street = address.street.strip()
    stored.house_number = address.house_number.strip()
    stored.house_number_suffix = (
        address.house_number_suffix.strip() if address.house_number_suffix else None
    )
    stored.postal_code = address.postal_code.strip()
    stored.city = address.city.strip()
    stored.country = address.country
    stored.phone = address.phone.strip() if address.phone else None
    order.delivery_address = stored
    return stored


@router.post("/orders", response_model=AdviceOrderResponse)
def receive_advice_order(
    payload: AdviceOrderRequest,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_sales_request),
) -> AdviceOrderResponse:
    """Take in one paid delivery order from the advice app, to observe.

    Picking and the shipping-label gate both hang off an ``Order`` row, so a
    delivery order has to become one here. It is born ``observed``: visible in
    Kanalen, and filtered out of the order list, Scan & Boek and the week
    planning. Nothing reserves or deducts stock — that is what observing means,
    and it is why this can ship before a delivery flow exists.

    Idempotent on ``(organization, channel, external_order_id)``: the advice app
    retries. A retry refreshes the address and the lines while the order is still
    observed, because until someone acts on it the newest version is the true
    one. Once it is no longer observed the order is left exactly as it is — by
    then a human is working on it, and overwriting under their hands is worse than
    a stale address they can see.

    Personal data: ``delivery_address`` is the only customer detail Dockscan keeps
    about a webshop buyer. It is here because a parcel cannot be addressed without
    it, it lives in its own table so it can be purged on its own, and it is never
    used for anything but this shipment.
    """
    if db.get(Organization, organization_id) is None:
        raise HTTPException(
            503,
            "Advice stock organization is not configured correctly",
        )

    connection = advice_connection(db, organization_id)
    try:
        assert_advice_observing(connection)
    except AdviceChannelNotObserving as exc:
        raise HTTPException(409, str(exc)) from exc

    # One product may arrive on several lines. Collapse them so the order books
    # one line per product, the same way a counter sale does.
    requested: dict[str, int] = {}
    product_order: list[str] = []
    for line in payload.lines:
        if line.source_product_id not in requested:
            product_order.append(line.source_product_id)
            requested[line.source_product_id] = 0
        requested[line.source_product_id] += line.quantity

    skus = {
        sku.source_product_id: sku
        for sku in db.query(SKU)
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
            SKU.source_product_id.in_(list(requested)),
        )
        .all()
    }

    def _load_order() -> Order | None:
        return (
            db.query(Order)
            .filter(
                Order.organization_id == organization_id,
                Order.channel == ADVICE_CHANNEL,
                Order.external_id == payload.external_order_id,
            )
            .with_for_update()
            .first()
        )

    order = _load_order()
    created = order is None
    if order is None:
        candidate = Order(
            organization_id=organization_id,
            channel=ADVICE_CHANNEL,
            external_id=payload.external_order_id,
            reference=f"ADV-{uuid.uuid4().hex[:8].upper()}",
            channel_reference=payload.order_reference,
            status="observed",
            inventory_location=payload.inventory_location,
            ordered_at=payload.ordered_at,
            created_by=None,
            delivery_week=None,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            order = candidate
        except IntegrityError:
            # Two retries of the same order raced on the unique
            # (organization, channel, external_id) index; the other one won.
            # Rolling back the savepoint already made the loser transient.
            order = _load_order()
            if order is None:
                raise
            created = False

    writable = created or order.status == "observed"
    if writable:
        order.channel_reference = payload.order_reference
        if payload.ordered_at is not None:
            order.ordered_at = payload.ordered_at
        _write_delivery_address(order, payload.delivery_address)

    matched: list[AdviceOrderMatchedLine] = []
    unmatched: list[str] = []
    lines_by_sku = {line.sku_id: line for line in order.lines}
    seen_sku_ids: set[int] = set()
    for product_id in product_order:
        sku = skus.get(product_id)
        if sku is None:
            unmatched.append(product_id)
            continue
        quantity = requested[product_id]
        matched.append(
            AdviceOrderMatchedLine(
                source_product_id=product_id,
                sku_code=sku.sku_code,
                quantity=quantity,
            )
        )
        seen_sku_ids.add(sku.id)
        if not writable:
            continue
        line = lines_by_sku.get(sku.id)
        if line is None:
            line = OrderLine(order_id=order.id, sku_id=sku.id)
            db.add(line)
        line.quantity = quantity
        # Channel orders carry the buyer's name without a Dockscan customer row;
        # there is no account here to point at.
        line.klant = payload.delivery_address.recipient_name.strip()
        line.customer_id = None
    if writable:
        for sku_id, line in lines_by_sku.items():
            if sku_id not in seen_sku_ids:
                db.delete(line)

    # The reconciliation view under Kanalen reads sync logs, not orders, so an
    # order the catalogue could not fully match still has to leave a trace. Upsert
    # rather than append: a retry must not grow the table.
    log = (
        db.query(ChannelSyncLog)
        .filter(
            ChannelSyncLog.organization_id == organization_id,
            ChannelSyncLog.channel == ADVICE_CHANNEL,
            ChannelSyncLog.external_id == payload.external_order_id,
        )
        .first()
    )
    if log is None:
        log = ChannelSyncLog(
            organization_id=organization_id,
            channel=ADVICE_CHANNEL,
            external_id=payload.external_order_id,
        )
        db.add(log)
    log.action = "created" if created else "updated"
    log.matched_lines = len(matched)
    # The column is named for EANs because Shopify and bol match on them. Advice
    # products are matched on their product id instead; the view only prints the
    # values, so they go in the same field rather than a near-duplicate column.
    log.unmatched_eans = json.dumps(unmatched)
    log.synced_at = datetime.datetime.utcnow()
    connection.last_synced_at = datetime.datetime.utcnow()

    db.commit()
    return AdviceOrderResponse(
        external_order_id=payload.external_order_id,
        order_id=order.id,
        reference=order.reference,
        status=order.status,
        duplicate=not created,
        matched=matched,
        unmatched=unmatched,
    )
