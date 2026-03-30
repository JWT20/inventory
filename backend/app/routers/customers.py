"""Customer management: CRUD for customers and their SKU catalogs."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from sqlalchemy.exc import IntegrityError

from app.models import Customer, CustomerSKU, OrderLine, Organization, User
from app.schemas import CustomerCreate, CustomerResponse

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
        sku_ids=[link.sku_id for link in customer.sku_links],
        created_at=customer.created_at,
    )


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    db: Session = Depends(get_db),
    user: User = Depends(_require_org_user),
):
    query = db.query(Customer).order_by(Customer.name)
    if not user.is_platform_admin:
        query = query.filter(Customer.organization_id == user.organization_id)
    return [_customer_to_response(c) for c in query.all()]


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
    customer = Customer(name=name, organization_id=org_id)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return _customer_to_response(customer)


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Access denied")

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
