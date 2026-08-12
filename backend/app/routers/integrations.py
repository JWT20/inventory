"""Server-to-server integration endpoints."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import (
    AdviceReservation,
    AdviceReservationLine,
    AdviceSale,
    AdviceSaleLine,
    InventoryBalance,
    Organization,
    SKU,
)
from app.schemas import (
    AdviceReservationLineResponse,
    AdviceReservationRequest,
    AdviceReservationResponse,
    AdviceSaleAppliedLine,
    AdviceSaleRequest,
    AdviceSaleResponse,
    AdviceStockItem,
    AdviceStockResponse,
)
from app.services.stock import adjust_reservation, apply_stock_movement

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
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_stock_request),
) -> AdviceStockResponse:
    """Return available bottle stock for the configured organization."""
    if db.get(Organization, organization_id) is None:
        raise HTTPException(
            503,
            "Advice stock organization is not configured correctly",
        )

    rows = (
        db.query(
            SKU.source_product_id,
            SKU.sku_code,
            SKU.active,
            InventoryBalance.quantity_on_hand,
            InventoryBalance.quantity_reserved,
        )
        .outerjoin(
            InventoryBalance,
            and_(
                InventoryBalance.sku_id == SKU.id,
                InventoryBalance.organization_id == organization_id,
                InventoryBalance.inventory_location == "store",
            ),
        )
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
        )
        .order_by(SKU.sku_code)
        .all()
    )

    response.headers["Cache-Control"] = "no-store"
    return AdviceStockResponse(
        items=[
            AdviceStockItem(
                source_product_id=source_product_id,
                sku_code=sku_code,
                is_bottle=True,
                quantity_available=(
                    max(
                        (quantity_on_hand or 0) - (quantity_reserved or 0),
                        0,
                    )
                    if active
                    else 0
                ),
            )
            for (
                source_product_id,
                sku_code,
                active,
                quantity_on_hand,
                quantity_reserved,
            ) in rows
        ]
    )


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


def _reservation_response(
    reservation: AdviceReservation, *, duplicate: bool
) -> AdviceReservationResponse:
    return AdviceReservationResponse(
        external_order_id=reservation.external_order_id,
        order_reference=reservation.order_reference,
        fulfillment_method="pickup",
        inventory_location="store",
        status=reservation.status,
        duplicate=duplicate,
        lines=[
            AdviceReservationLineResponse(
                source_product_id=line.sku.source_product_id,
                sku_code=line.sku.sku_code,
                quantity=line.quantity,
            )
            for line in sorted(reservation.lines, key=lambda item: item.sku.sku_code)
        ],
    )


def _locked_reservation(
    db: Session, organization_id: int, external_order_id: str
) -> AdviceReservation | None:
    return (
        db.query(AdviceReservation)
        .options(joinedload(AdviceReservation.lines).joinedload(AdviceReservationLine.sku))
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
    """Reserve store bottles before wijnadvies1 starts the online payment."""
    if db.get(Organization, organization_id) is None:
        raise HTTPException(503, "Advice stock organization is not configured correctly")

    requested: dict[str, int] = {}
    for line in payload.lines:
        requested[line.source_product_id] = (
            requested.get(line.source_product_id, 0) + line.quantity
        )

    existing = _locked_reservation(db, organization_id, payload.external_order_id)
    if existing:
        existing_lines = {
            line.sku.source_product_id: line.quantity for line in existing.lines
        }
        if existing_lines != requested:
            raise HTTPException(
                409,
                "Deze order-ID bestaat al met andere productregels",
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
        fulfillment_method="pickup",
        inventory_location="store",
        status="active",
    )
    db.add(reservation)
    try:
        db.flush()
        for product_id in sorted(requested):
            sku = skus[product_id]
            quantity = requested[product_id]
            adjust_reservation(
                db,
                sku_id=sku.id,
                organization_id=organization_id,
                inventory_location="store",
                delta=quantity,
                require_available=True,
            )
            db.add(
                AdviceReservationLine(
                    reservation_id=reservation.id,
                    sku_id=sku.id,
                    quantity=quantity,
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
    """Consume one pickup reservation when the customer receives the bottles."""
    reservation = _locked_reservation(db, organization_id, external_order_id)
    if not reservation:
        raise HTTPException(404, "Reservering niet gevonden")
    if reservation.status == "collected":
        return _reservation_response(reservation, duplicate=True)
    if reservation.status == "released":
        raise HTTPException(409, "Deze reservering is al vrijgegeven")

    for line in reservation.lines:
        adjust_reservation(
            db,
            sku_id=line.sku_id,
            organization_id=organization_id,
            inventory_location="store",
            delta=-line.quantity,
        )
        apply_stock_movement(
            db,
            sku_id=line.sku_id,
            organization_id=organization_id,
            inventory_location="store",
            quantity=-line.quantity,
            movement_type="sale",
            reference_type="advice_pickup",
            reference_id=reservation.id,
            note=f"pickup {external_order_id}",
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
    """Release store stock for a cancelled/refunded pickup order."""
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
            inventory_location="store",
            delta=-line.quantity,
        )
    reservation.status = "released"
    reservation.released_at = func.now()
    db.commit()
    return _reservation_response(reservation, duplicate=False)
