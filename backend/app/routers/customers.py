"""Customer management: CRUD for customers and their SKU catalogs."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from sqlalchemy.exc import IntegrityError

from app.models import Customer, CustomerSKU, OrderLine, Organization, SKU, User
from app.services.pricing import calc_effective_price
from app.schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerSKUAdd,
    CustomerSKUReorder,
    CustomerSKUResponse,
    CustomerUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customers", tags=["customers"])


def _require_customer_reader(user: User = Depends(get_current_user)) -> User:
    """Allow merchant users to read customers and customer users to read only themselves."""
    if user.is_platform_admin:
        return user
    if user.organization_id and user.role in ("owner", "member", "customer"):
        return user
    raise HTTPException(403, "Access denied")


def _customer_to_response(customer: Customer) -> CustomerResponse:
    ordered_links = sorted(
        customer.sku_links, key=lambda l: (l.sort_order, l.id)
    )
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
        delivery_days=customer.delivery_days,
        sku_ids=[link.sku_id for link in ordered_links],
        sku_count=len(ordered_links),
        created_at=customer.created_at,
    )


def _get_customer_or_404(
    customer_id: int, user: User, db: Session
) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if user.role == "customer" and not user.is_platform_admin:
        if not user.customer_id or customer.id != user.customer_id:
            raise HTTPException(403, "Geen toegang")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")
    return customer


# ── CRUD ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    db: Session = Depends(get_db),
    user: User = Depends(_require_customer_reader),
):
    if user.role == "customer" and not user.is_platform_admin:
        if not user.customer_id:
            return []
        customer = _get_customer_or_404(user.customer_id, user, db)
        return [_customer_to_response(customer)]

    query = db.query(Customer).order_by(Customer.name)
    if not user.is_platform_admin:
        query = query.filter(Customer.organization_id == user.organization_id)
    return [_customer_to_response(c) for c in query.all()]


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_require_customer_reader),
):
    customer = _get_customer_or_404(customer_id, user, db)
    return _customer_to_response(customer)


@router.post("", response_model=CustomerResponse, status_code=201)
def create_customer(
    body: CustomerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
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
    delivery_days = body.delivery_days
    if "delivery_days" not in body.model_fields_set and "delivery_day" in body.model_fields_set:
        delivery_days = [body.delivery_day]
    if (
        "delivery_days" in body.model_fields_set
        and "delivery_day" in body.model_fields_set
        and body.delivery_day not in delivery_days
    ):
        raise HTTPException(400, "Voorkeursleverdag moet in mogelijke leverdagen staan")
    delivery_day = body.delivery_day if body.delivery_day in delivery_days else delivery_days[0]

    customer = Customer(
        name=name,
        organization_id=org_id,
        show_prices=body.show_prices,
        discount_percentage=body.discount_percentage,
        delivery_day=delivery_day,
    )
    customer.delivery_days = delivery_days
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

    if body.delivery_days is not None:
        customer.delivery_days = body.delivery_days
        if customer.delivery_day not in customer.delivery_days:
            customer.delivery_day = customer.delivery_days[0]

    if body.delivery_day is not None:
        if body.delivery_day not in customer.delivery_days:
            raise HTTPException(400, "Voorkeursleverdag moet in mogelijke leverdagen staan")
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
    user: User = Depends(_require_customer_reader),
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
        .order_by(CustomerSKU.sort_order, SKU.name)
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
                effective_price=calc_effective_price(
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

    from sqlalchemy import func

    next_pos = (
        db.query(func.coalesce(func.max(CustomerSKU.sort_order), 0))
        .filter(CustomerSKU.customer_id == customer_id)
        .scalar()
    ) or 0

    added = 0
    for sku_id in body.sku_ids:
        exists = (
            db.query(CustomerSKU)
            .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
            .first()
        )
        if not exists:
            next_pos += 1
            db.add(
                CustomerSKU(
                    customer_id=customer_id,
                    sku_id=sku_id,
                    sort_order=next_pos,
                )
            )
            added += 1

    db.commit()
    return {"added": added}


@router.put("/{customer_id}/skus/reorder", status_code=204)
def reorder_customer_skus(
    customer_id: int,
    body: CustomerSKUReorder,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    _get_customer_or_404(customer_id, user, db)

    links = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id)
        .all()
    )
    by_sku = {link.sku_id: link for link in links}

    unknown = [sid for sid in body.sku_ids if sid not in by_sku]
    if unknown:
        raise HTTPException(
            400, f"SKU's niet in assortiment van klant: {sorted(unknown)}"
        )

    for pos, sku_id in enumerate(body.sku_ids, start=1):
        by_sku[sku_id].sort_order = pos

    db.commit()


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
