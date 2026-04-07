"""CRUD endpoints for product attribute definitions (kenmerken & kenmerk waardes).

Organizations can define their own attribute catalog (e.g. 'Druivensoort',
'Regio') with predefined allowed values (e.g. 'Cabernet Sauvignon', 'Merlot').
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from app.models import ProductAttribute, ProductAttributeValue, User
from app.schemas import (
    ProductAttributeCreate,
    ProductAttributeResponse,
    ProductAttributeUpdate,
    ProductAttributeValueCreate,
    ProductAttributeValueResponse,
    ProductAttributeValueUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/product-attributes", tags=["product-attributes"])


def _require_org(user: User) -> int:
    """Return the user's organization_id or raise 400."""
    if user.is_platform_admin and not user.organization_id:
        raise HTTPException(400, "Platform admins moeten een organisatie-context hebben")
    if not user.organization_id:
        raise HTTPException(403, "Gebruiker heeft geen organisatie")
    return user.organization_id


def _get_attribute(
    db: Session, attr_id: int, org_id: int,
) -> ProductAttribute:
    attr = (
        db.query(ProductAttribute)
        .options(selectinload(ProductAttribute.values))
        .filter(ProductAttribute.id == attr_id, ProductAttribute.organization_id == org_id)
        .first()
    )
    if not attr:
        raise HTTPException(404, "Kenmerk niet gevonden")
    return attr


# ── Attribute CRUD ──────────────────────────────────────────────────────────


@router.get("", response_model=list[ProductAttributeResponse])
def list_attributes(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all product attribute definitions for the user's organization."""
    org_id = _require_org(user)
    attrs = (
        db.query(ProductAttribute)
        .options(selectinload(ProductAttribute.values))
        .filter(ProductAttribute.organization_id == org_id)
        .order_by(ProductAttribute.name)
        .all()
    )
    return attrs


@router.post("", response_model=ProductAttributeResponse, status_code=201)
def create_attribute(
    data: ProductAttributeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Create a new attribute definition, optionally with initial values."""
    org_id = _require_org(user)

    existing = (
        db.query(ProductAttribute)
        .filter(
            ProductAttribute.organization_id == org_id,
            ProductAttribute.name == data.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"Kenmerk '{data.name}' bestaat al")

    attr = ProductAttribute(
        organization_id=org_id,
        name=data.name,
        description=data.description,
    )
    for v in data.values:
        attr.values.append(
            ProductAttributeValue(value=v.value, sort_order=v.sort_order)
        )

    db.add(attr)
    db.commit()
    db.refresh(attr)
    return attr


@router.get("/{attr_id}", response_model=ProductAttributeResponse)
def get_attribute(
    attr_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a single attribute definition with its values."""
    org_id = _require_org(user)
    return _get_attribute(db, attr_id, org_id)


@router.patch("/{attr_id}", response_model=ProductAttributeResponse)
def update_attribute(
    attr_id: int,
    data: ProductAttributeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Update attribute name or description."""
    org_id = _require_org(user)
    attr = _get_attribute(db, attr_id, org_id)

    if data.name is not None:
        conflict = (
            db.query(ProductAttribute)
            .filter(
                ProductAttribute.organization_id == org_id,
                ProductAttribute.name == data.name,
                ProductAttribute.id != attr_id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(409, f"Kenmerk '{data.name}' bestaat al")
        attr.name = data.name

    if data.description is not None:
        attr.description = data.description

    db.commit()
    db.refresh(attr)
    return attr


@router.delete("/{attr_id}", status_code=204)
def delete_attribute(
    attr_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Delete an attribute definition and all its values."""
    org_id = _require_org(user)
    attr = _get_attribute(db, attr_id, org_id)
    db.delete(attr)
    db.commit()


# ── Attribute Value CRUD ────────────────────────────────────────────────────


@router.post(
    "/{attr_id}/values",
    response_model=ProductAttributeValueResponse,
    status_code=201,
)
def add_attribute_value(
    attr_id: int,
    data: ProductAttributeValueCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Add a new allowed value to an attribute."""
    org_id = _require_org(user)
    attr = _get_attribute(db, attr_id, org_id)

    existing = (
        db.query(ProductAttributeValue)
        .filter(
            ProductAttributeValue.attribute_id == attr_id,
            ProductAttributeValue.value == data.value,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"Waarde '{data.value}' bestaat al voor dit kenmerk")

    val = ProductAttributeValue(
        attribute_id=attr_id,
        value=data.value,
        sort_order=data.sort_order,
    )
    db.add(val)
    db.commit()
    db.refresh(val)
    return val


@router.patch(
    "/{attr_id}/values/{value_id}",
    response_model=ProductAttributeValueResponse,
)
def update_attribute_value(
    attr_id: int,
    value_id: int,
    data: ProductAttributeValueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Update an attribute value's text or sort order."""
    org_id = _require_org(user)
    _get_attribute(db, attr_id, org_id)  # ownership check

    val = (
        db.query(ProductAttributeValue)
        .filter(
            ProductAttributeValue.id == value_id,
            ProductAttributeValue.attribute_id == attr_id,
        )
        .first()
    )
    if not val:
        raise HTTPException(404, "Kenmerk waarde niet gevonden")

    if data.value is not None:
        conflict = (
            db.query(ProductAttributeValue)
            .filter(
                ProductAttributeValue.attribute_id == attr_id,
                ProductAttributeValue.value == data.value,
                ProductAttributeValue.id != value_id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(409, f"Waarde '{data.value}' bestaat al voor dit kenmerk")
        val.value = data.value

    if data.sort_order is not None:
        val.sort_order = data.sort_order

    db.commit()
    db.refresh(val)
    return val


@router.delete("/{attr_id}/values/{value_id}", status_code=204)
def delete_attribute_value(
    attr_id: int,
    value_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Delete a single attribute value."""
    org_id = _require_org(user)
    _get_attribute(db, attr_id, org_id)  # ownership check

    val = (
        db.query(ProductAttributeValue)
        .filter(
            ProductAttributeValue.id == value_id,
            ProductAttributeValue.attribute_id == attr_id,
        )
        .first()
    )
    if not val:
        raise HTTPException(404, "Kenmerk waarde niet gevonden")

    db.delete(val)
    db.commit()
