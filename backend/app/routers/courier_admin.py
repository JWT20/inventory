"""Admin-facing courier billing: rate-card CRUD and settlement runs.

Platform-admin only. Couriers never see the platform/courier split or other
couriers' settlements.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import (
    CourierRateCard,
    CourierSettlement,
    CourierSettlementLine,
    Customer,
    Organization,
    User,
)
from app.routers.courier import month_bounds
from app.schemas import (
    RateCardCreate,
    RateCardResponse,
    RateCardUpdate,
    SettlementCreate,
    SettlementResponse,
)
from app.services.courier_billing import compute_courier_groups
from app.services.rate_resolver import ranges_overlap

router = APIRouter(prefix="/courier", tags=["courier-admin"])


# ---------------------------------------------------------------------------
# Rate cards
# ---------------------------------------------------------------------------
def _rate_card_response(db: Session, card: CourierRateCard) -> RateCardResponse:
    courier = db.get(User, card.courier_id) if card.courier_id else None
    customer = db.get(Customer, card.customer_id) if card.customer_id else None
    org = db.get(Organization, card.organization_id) if card.organization_id else None
    return RateCardResponse(
        id=card.id,
        courier_id=card.courier_id,
        customer_id=card.customer_id,
        organization_id=card.organization_id,
        service_type=card.service_type,
        unit_type=card.unit_type,
        charge_cents=card.charge_cents,
        platform_cents=card.platform_cents,
        courier_cents=card.courier_cents,
        effective_from=card.effective_from,
        effective_until=card.effective_until,
        courier_name=courier.username if courier else None,
        customer_name=customer.name if customer else None,
        organization_name=org.name if org else None,
    )


def _validate_scope(db: Session, data) -> None:
    """Reject cards whose scope fields can never match a real booking:
    a non-courier courier_id, an unknown customer/org, or a customer that
    does not belong to the given organization."""
    if data.courier_id is not None:
        u = db.get(User, data.courier_id)
        if not u or u.role != "courier":
            raise HTTPException(422, "courier_id verwijst niet naar een koerier")
    customer = None
    if data.customer_id is not None:
        customer = db.get(Customer, data.customer_id)
        if not customer:
            raise HTTPException(422, "Onbekende klant")
    if data.organization_id is not None:
        if not db.get(Organization, data.organization_id):
            raise HTTPException(422, "Onbekende organisatie")
    if customer is not None and data.organization_id is not None:
        if customer.organization_id != data.organization_id:
            raise HTTPException(
                422, "Klant hoort niet bij de opgegeven organisatie"
            )


def _assert_no_overlap(db: Session, data, exclude_id: int | None = None) -> None:
    """Reject a card whose date range overlaps an existing same-scope card."""
    same_scope = (
        db.query(CourierRateCard)
        .filter(
            CourierRateCard.courier_id == data.courier_id,
            CourierRateCard.customer_id == data.customer_id,
            CourierRateCard.organization_id == data.organization_id,
            CourierRateCard.service_type == data.service_type,
        )
        .all()
    )
    for existing in same_scope:
        if exclude_id is not None and existing.id == exclude_id:
            continue
        if ranges_overlap(
            data.effective_from, data.effective_until,
            existing.effective_from, existing.effective_until,
        ):
            raise HTTPException(
                409,
                "Overlappende tariefkaart bestaat al voor deze scope en periode "
                f"(kaart #{existing.id})",
            )


@router.get("/rate-cards", response_model=list[RateCardResponse])
def list_rate_cards(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    cards = (
        db.query(CourierRateCard)
        .order_by(CourierRateCard.effective_from.desc(), CourierRateCard.id.desc())
        .all()
    )
    return [_rate_card_response(db, c) for c in cards]


@router.post("/rate-cards", response_model=RateCardResponse, status_code=201)
def create_rate_card(
    data: RateCardCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    _validate_scope(db, data)
    _assert_no_overlap(db, data)
    card = CourierRateCard(
        courier_id=data.courier_id,
        customer_id=data.customer_id,
        organization_id=data.organization_id,
        service_type=data.service_type,
        unit_type=data.unit_type,
        charge_cents=data.charge_cents,
        platform_cents=data.platform_cents,
        courier_cents=data.courier_cents,
        effective_from=data.effective_from,
        effective_until=data.effective_until,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return _rate_card_response(db, card)


@router.patch("/rate-cards/{card_id}", response_model=RateCardResponse)
def update_rate_card(
    card_id: int,
    data: RateCardUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    card = db.get(CourierRateCard, card_id)
    if not card:
        raise HTTPException(404, "Tariefkaart niet gevonden")
    _validate_scope(db, data)
    _assert_no_overlap(db, data, exclude_id=card_id)
    card.courier_id = data.courier_id
    card.customer_id = data.customer_id
    card.organization_id = data.organization_id
    card.service_type = data.service_type
    card.unit_type = data.unit_type
    card.charge_cents = data.charge_cents
    card.platform_cents = data.platform_cents
    card.courier_cents = data.courier_cents
    card.effective_from = data.effective_from
    card.effective_until = data.effective_until
    db.commit()
    db.refresh(card)
    return _rate_card_response(db, card)


@router.delete("/rate-cards/{card_id}", status_code=204)
def delete_rate_card(
    card_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    card = db.get(CourierRateCard, card_id)
    if not card:
        raise HTTPException(404, "Tariefkaart niet gevonden")
    # Settlement lines snapshot their amounts, so deleting a card never alters
    # past settlements (rate_card_id is set to NULL there).
    db.delete(card)
    db.commit()


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------
def _settlement_response(
    db: Session, s: CourierSettlement, include_lines: bool = True
) -> SettlementResponse:
    courier = db.get(User, s.courier_id)
    return SettlementResponse(
        id=s.id,
        courier_id=s.courier_id,
        courier_name=courier.username if courier else None,
        period_month=s.period_month,
        status=s.status,
        total_units=s.total_units,
        total_charge_cents=s.total_charge_cents,
        total_platform_cents=s.total_platform_cents,
        total_courier_cents=s.total_courier_cents,
        created_at=s.created_at,
        approved_at=s.approved_at,
        paid_at=s.paid_at,
        lines=list(s.lines) if include_lines else [],
    )


@router.post("/settlements", response_model=SettlementResponse, status_code=201)
def create_settlement(
    data: SettlementCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    courier = db.get(User, data.courier_id)
    if not courier or courier.role != "courier":
        raise HTTPException(404, "Koerier niet gevonden")

    start, end, month_label = month_bounds(data.month)

    existing = (
        db.query(CourierSettlement)
        .filter(
            CourierSettlement.courier_id == data.courier_id,
            CourierSettlement.period_month == month_label,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            409, f"Er bestaat al een afrekening voor deze koerier in {month_label}"
        )

    groups = compute_courier_groups(db, data.courier_id, start, end)

    # Refuse to freeze money against units that have no matching tariff (they
    # would be billed at zero). The admin can override deliberately.
    unrated = [g for g in groups if g.rate_card_id is None]
    if unrated and not data.allow_unrated:
        names = ", ".join(sorted({f"{g.customer_name} ({g.service_type})" for g in unrated}))
        raise HTTPException(
            422,
            f"Geen tarief gevonden voor: {names}. Voeg een tariefkaart toe of zet "
            "allow_unrated aan om toch af te rekenen.",
        )

    settlement = CourierSettlement(
        courier_id=data.courier_id,
        period_month=month_label,
        status="draft",
        created_by=user.id,
    )
    db.add(settlement)
    db.flush()

    total_units = total_charge = total_platform = total_courier = 0
    for g in groups:
        line_charge = g.units * g.charge_cents
        line_platform = g.units * g.platform_cents
        line_courier = g.units * g.courier_cents
        db.add(
            CourierSettlementLine(
                settlement_id=settlement.id,
                customer_id=g.customer_id,
                customer_name=g.customer_name,
                service_type=g.service_type,
                unit_type=g.unit_type,
                units=g.units,
                rate_card_id=g.rate_card_id,
                scope=g.scope,
                charge_cents=g.charge_cents,
                platform_cents=g.platform_cents,
                courier_cents=g.courier_cents,
                line_charge_cents=line_charge,
                line_platform_cents=line_platform,
                line_courier_cents=line_courier,
            )
        )
        total_units += g.units
        total_charge += line_charge
        total_platform += line_platform
        total_courier += line_courier

    settlement.total_units = total_units
    settlement.total_charge_cents = total_charge
    settlement.total_platform_cents = total_platform
    settlement.total_courier_cents = total_courier
    db.commit()
    db.refresh(settlement)
    return _settlement_response(db, settlement)


@router.get("/settlements", response_model=list[SettlementResponse])
def list_settlements(
    courier_id: int | None = Query(None),
    month: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    query = db.query(CourierSettlement)
    if courier_id is not None:
        query = query.filter(CourierSettlement.courier_id == courier_id)
    if month is not None:
        query = query.filter(CourierSettlement.period_month == month)
    settlements = query.order_by(
        CourierSettlement.period_month.desc(), CourierSettlement.id.desc()
    ).all()
    return [_settlement_response(db, s, include_lines=False) for s in settlements]


@router.get("/settlements/{settlement_id}", response_model=SettlementResponse)
def get_settlement(
    settlement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    s = db.get(CourierSettlement, settlement_id)
    if not s:
        raise HTTPException(404, "Afrekening niet gevonden")
    return _settlement_response(db, s)


@router.post("/settlements/{settlement_id}/approve", response_model=SettlementResponse)
def approve_settlement(
    settlement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    s = db.get(CourierSettlement, settlement_id)
    if not s:
        raise HTTPException(404, "Afrekening niet gevonden")
    if s.status != "draft":
        raise HTTPException(400, f"Kan niet goedkeuren (status: {s.status})")
    s.status = "approved"
    s.approved_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(s)
    return _settlement_response(db, s)


@router.post("/settlements/{settlement_id}/pay", response_model=SettlementResponse)
def pay_settlement(
    settlement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    s = db.get(CourierSettlement, settlement_id)
    if not s:
        raise HTTPException(404, "Afrekening niet gevonden")
    if s.status != "approved":
        raise HTTPException(400, f"Kan niet als betaald markeren (status: {s.status})")
    s.status = "paid"
    s.paid_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(s)
    return _settlement_response(db, s)


@router.delete("/settlements/{settlement_id}", status_code=204)
def delete_settlement(
    settlement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    s = db.get(CourierSettlement, settlement_id)
    if not s:
        raise HTTPException(404, "Afrekening niet gevonden")
    if s.status != "draft":
        raise HTTPException(400, "Alleen concept-afrekeningen kunnen verwijderd worden")
    db.delete(s)
    db.commit()
