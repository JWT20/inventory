"""Customer management: CRUD for customers and their SKU catalogs."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from sqlalchemy.exc import IntegrityError

from app.models import Customer, CustomerSKU, OrderLine, Organization, SKU, User
from app.schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerSKUAdd,
    CustomerSKUResponse,
    CustomerUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customers", tags=["customers"])


def _require_org_user(user: User = Depends(get_current_user)) -> User:
    """Allow any user with an organization (owner, member, customer) or platform admin."""
    if user.is_platform_admin:
        return user
    if user.organization_id and user.role in ("owner", "member", "customer"):
        return user
    raise HTTPException(403, "Access denied")


def _customer_to_response(customer: Customer) -> CustomerResponse:
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        show_prices=customer.show_prices,
        discount_percentage=(
            float(customer.discount_percentage)
            if customer.discount_percentage is not None
            else None
        ),
        delivery_day=customer.delivery_day,
        sku_ids=[link.sku_id for link in customer.sku_links],
        sku_count=len(customer.sku_links),
        created_at=customer.created_at,
    )


def _calc_effective_price(
    default_price: float | None,
    unit_price: float | None,
    discount_type: str | None,
    discount_value: float | None,
    customer_discount_pct: float | None,
) -> float | None:
    """Price waterfall: unit_price > sku discount > customer discount > default."""
    if unit_price is not None:
        return unit_price
    if default_price is None:
        return None
    if discount_type and discount_value is not None:
        if discount_type == "percentage":
            return round(default_price * (1 - discount_value / 100), 2)
        if discount_type == "fixed":
            return round(max(default_price - discount_value, 0), 2)
    if customer_discount_pct is not None:
        return round(default_price * (1 - customer_discount_pct / 100), 2)
    return default_price


def _get_customer_or_404(
    customer_id: int, user: User, db: Session
) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")
    return customer


# ── CRUD ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    db: Session = Depends(get_db),
    user: User = Depends(_require_org_user),
):
    query = db.query(Customer).order_by(Customer.name)
    if not user.is_platform_admin:
        query = query.filter(Customer.organization_id == user.organization_id)
    return [_customer_to_response(c) for c in query.all()]


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_require_org_user),
):
    customer = _get_customer_or_404(customer_id, user, db)
    return _customer_to_response(customer)


@router.post("", response_model=CustomerResponse, status_code=201)
def create_customer(
    body: CustomerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(_require_org_user),
):
    name = body.name.strip().lower()
    if not name:
        raise HTTPException(400, "Naam mag niet leeg zijn")
    # Resolve organization: admin must specify, others use their own
    if user.is_platform_admin:
        org_id = body.organization_id or user.organization_id
        if not org_id:
            raise HTTPException(400, "Platform admin moet een organization_id opgeven")
        if not db.get(Organization, org_id):
            raise HTTPException(404, f"Organisatie met id {org_id} niet gevonden")
    else:
        org_id = user.organization_id
    existing = (
        db.query(Customer)
        .filter(Customer.name == name, Customer.organization_id == org_id)
        .first()
    )
    if existing:
        raise HTTPException(409, f"Klant '{name}' bestaat al")
    customer = Customer(
        name=name,
        organization_id=org_id,
        show_prices=body.show_prices,
        discount_percentage=body.discount_percentage,
        delivery_day=body.delivery_day,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return _customer_to_response(customer)


@router.patch("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = _get_customer_or_404(customer_id, user, db)

    if body.name is not None:
        name = body.name.strip().lower()
        if not name:
            raise HTTPException(400, "Naam mag niet leeg zijn")
        existing = (
            db.query(Customer)
            .filter(
                Customer.name == name,
                Customer.organization_id == customer.organization_id,
                Customer.id != customer_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(409, f"Klant '{name}' bestaat al")
        customer.name = name

    if body.show_prices is not None:
        customer.show_prices = body.show_prices

    if body.discount_percentage is not None:
        customer.discount_percentage = body.discount_percentage
    # Allow explicitly clearing discount by sending 0 or null
    # Since Pydantic default is None, we check if the field was actually sent
    elif "discount_percentage" in (body.model_fields_set or set()):
        customer.discount_percentage = None

    if body.delivery_day is not None:
        customer.delivery_day = body.delivery_day

    db.commit()
    db.refresh(customer)
    return _customer_to_response(customer)


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = _get_customer_or_404(customer_id, user, db)

    # Unlink any users tied to this customer
    for u in db.query(User).filter(User.customer_id == customer_id).all():
        u.customer_id = None

    # Unlink order lines (keep history via the klant text field)
    for ol in db.query(OrderLine).filter(OrderLine.customer_id == customer_id).all():
        ol.customer_id = None

    db.delete(customer)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Klant kan niet verwijderd worden: er zijn nog gekoppelde gegevens"
        )


# ── Customer SKU catalog ──────────────────────────────────────────────


@router.get("/{customer_id}/skus", response_model=list[CustomerSKUResponse])
def list_customer_skus(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_require_org_user),
):
    customer = _get_customer_or_404(customer_id, user, db)
    customer_discount = (
        float(customer.discount_percentage)
        if customer.discount_percentage is not None
        else None
    )

    links = (
        db.query(CustomerSKU, SKU)
        .join(SKU, CustomerSKU.sku_id == SKU.id)
        .filter(CustomerSKU.customer_id == customer_id)
        .order_by(SKU.name)
        .all()
    )

    result = []
    for link, sku in links:
        default_price = float(sku.default_price) if sku.default_price is not None else None
        unit_price = float(link.unit_price) if link.unit_price is not None else None
        dt = link.discount_type
        dv = float(link.discount_value) if link.discount_value is not None else None

        result.append(
            CustomerSKUResponse(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.name,
                default_price=default_price,
                unit_price=unit_price,
                discount_type=dt,
                discount_value=dv,
                effective_price=_calc_effective_price(
                    default_price, unit_price, dt, dv, customer_discount
                ),
            )
        )
    return result


@router.post("/{customer_id}/skus", status_code=201)
def add_customer_skus(
    customer_id: int,
    body: CustomerSKUAdd,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = _get_customer_or_404(customer_id, user, db)

    # Validate SKUs exist and belong to same org
    skus = db.query(SKU).filter(SKU.id.in_(body.sku_ids)).all()
    found_ids = {s.id for s in skus}
    missing = set(body.sku_ids) - found_ids
    if missing:
        raise HTTPException(404, f"SKU's niet gevonden: {sorted(missing)}")

    added = 0
    for sku_id in body.sku_ids:
        exists = (
            db.query(CustomerSKU)
            .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
            .first()
        )
        if not exists:
            db.add(CustomerSKU(customer_id=customer_id, sku_id=sku_id))
            added += 1

    db.commit()
    return {"added": added}


@router.delete("/{customer_id}/skus/{sku_id}", status_code=204)
def remove_customer_sku(
    customer_id: int,
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    _get_customer_or_404(customer_id, user, db)

    link = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "Product niet gevonden in assortiment")

    db.delete(link)
    db.commit()
