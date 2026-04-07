"""Supplier management: CRUD for leveranciers."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from app.models import Supplier, User
from app.schemas import SupplierCreate, SupplierResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("", response_model=list[SupplierResponse])
def list_suppliers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Supplier)
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(Supplier.organization_id == user.organization_id)
        else:
            return []
    return query.order_by(Supplier.name).all()


@router.post("", response_model=SupplierResponse, status_code=201)
def create_supplier(
    data: SupplierCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    org_id = user.organization_id
    if not user.is_platform_admin and not org_id:
        raise HTTPException(400, "Gebruiker heeft geen organisatie")

    existing = (
        db.query(Supplier)
        .filter(Supplier.organization_id == org_id, Supplier.name == data.name)
        .first()
    )
    if existing:
        raise HTTPException(409, f"Leverancier '{data.name}' bestaat al")

    supplier = Supplier(name=data.name, organization_id=org_id)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.patch("/{supplier_id}", response_model=SupplierResponse)
def update_supplier(
    supplier_id: int,
    data: SupplierCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Leverancier niet gevonden")
    if not user.is_platform_admin and supplier.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    conflict = (
        db.query(Supplier)
        .filter(
            Supplier.organization_id == supplier.organization_id,
            Supplier.name == data.name,
            Supplier.id != supplier_id,
        )
        .first()
    )
    if conflict:
        raise HTTPException(409, f"Leverancier '{data.name}' bestaat al")

    supplier.name = data.name
    db.commit()
    db.refresh(supplier)
    return supplier


@router.delete("/{supplier_id}", status_code=204)
def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Leverancier niet gevonden")
    if not user.is_platform_admin and supplier.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    db.delete(supplier)
    db.commit()
