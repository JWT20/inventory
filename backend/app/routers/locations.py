"""Pick-location management — courier-only (warehouse worker).

Barcode products get a physical shelf location (row/cabinet/shelf) with a
scannable code. Vision/wine products are never linked here — they are picked by
photo, not by shelf. The whole router is gated to platform admin + courier via
``require_warehouse``; owners/members never see it.

This is PR 1 of 2: the data + management screen. The pick flow itself (scanning
the location before its EANs) is a follow-up that builds on this.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.auth import require_warehouse
from app.database import get_db
from app.models import SKU, Location, SKULocation, User
from app.schemas import (
    AvailableSKU,
    LinkSKURequest,
    LocationCreate,
    LocationResponse,
    LocationSKU,
    LocationUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/locations",
    tags=["locations"],
    dependencies=[Depends(require_warehouse)],
)


def _to_response(location: Location) -> LocationResponse:
    skus = [
        LocationSKU(
            sku_id=link.sku_id,
            sku_code=link.sku.sku_code,
            name=link.sku.name,
            ean=link.sku.ean,
            organization_name=link.sku.organization.name if link.sku.organization else None,
            is_primary=link.is_primary,
        )
        for link in location.sku_links
    ]
    skus.sort(key=lambda s: s.sku_code)
    return LocationResponse(
        id=location.id,
        code=location.code,
        rij=location.rij,
        kast=location.kast,
        plank=location.plank,
        active=location.active,
        created_at=location.created_at,
        skus=skus,
    )


def _load(db: Session, location_id: int) -> Location:
    location = (
        db.query(Location)
        .options(
            joinedload(Location.sku_links)
            .joinedload(SKULocation.sku)
            .joinedload(SKU.organization)
        )
        .filter(Location.id == location_id)
        .first()
    )
    if not location:
        raise HTTPException(404, "Locatie niet gevonden")
    return location


@router.get("", response_model=list[LocationResponse])
def list_locations(db: Session = Depends(get_db)):
    """All pick locations, sorted for a natural walking route (row/cabinet/shelf)."""
    locations = (
        db.query(Location)
        .options(
            joinedload(Location.sku_links)
            .joinedload(SKULocation.sku)
            .joinedload(SKU.organization)
        )
        .order_by(Location.rij, Location.kast, Location.plank, Location.code)
        .all()
    )
    return [_to_response(loc) for loc in locations]


@router.post("", response_model=LocationResponse, status_code=201)
def create_location(data: LocationCreate, db: Session = Depends(get_db)):
    code = data.code.strip()
    if db.query(Location).filter(Location.code == code).first():
        raise HTTPException(409, f"Locatie '{code}' bestaat al")
    location = Location(
        code=code,
        rij=(data.rij or None),
        kast=(data.kast or None),
        plank=(data.plank or None),
    )
    db.add(location)
    db.commit()
    return _to_response(_load(db, location.id))


@router.patch("/{location_id}", response_model=LocationResponse)
def update_location(
    location_id: int, data: LocationUpdate, db: Session = Depends(get_db)
):
    location = _load(db, location_id)
    if data.code is not None:
        code = data.code.strip()
        conflict = (
            db.query(Location)
            .filter(Location.code == code, Location.id != location_id)
            .first()
        )
        if conflict:
            raise HTTPException(409, f"Locatie '{code}' bestaat al")
        location.code = code
    if data.rij is not None:
        location.rij = data.rij or None
    if data.kast is not None:
        location.kast = data.kast or None
    if data.plank is not None:
        location.plank = data.plank or None
    if data.active is not None:
        location.active = data.active
    db.commit()
    return _to_response(_load(db, location_id))


@router.delete("/{location_id}", status_code=204)
def delete_location(location_id: int, db: Session = Depends(get_db)):
    location = _load(db, location_id)
    db.delete(location)
    db.commit()


@router.post("/{location_id}/skus", response_model=LocationResponse)
def link_sku(
    location_id: int, data: LinkSKURequest, db: Session = Depends(get_db)
):
    """Link a barcode product to this location. Rejects vision/wine products."""
    location = _load(db, location_id)
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "Product niet gevonden")
    if sku.product_type != "barcode":
        raise HTTPException(
            400, "Alleen barcode-producten kunnen een locatie krijgen"
        )
    existing = (
        db.query(SKULocation)
        .filter(
            SKULocation.location_id == location_id,
            SKULocation.sku_id == data.sku_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"{sku.name} staat al op deze locatie")
    db.add(
        SKULocation(
            location_id=location_id, sku_id=data.sku_id, is_primary=data.is_primary
        )
    )
    db.commit()
    return _to_response(_load(db, location_id))


@router.delete("/{location_id}/skus/{sku_id}", status_code=204)
def unlink_sku(location_id: int, sku_id: int, db: Session = Depends(get_db)):
    link = (
        db.query(SKULocation)
        .filter(
            SKULocation.location_id == location_id, SKULocation.sku_id == sku_id
        )
        .first()
    )
    if not link:
        raise HTTPException(404, "Koppeling niet gevonden")
    db.delete(link)
    db.commit()


@router.get("/available-skus", response_model=list[AvailableSKU])
def available_skus(q: str = "", db: Session = Depends(get_db)):
    """Barcode products the courier can link, across all merchants (courier has
    no org). Vision/wine products are excluded — they are never shelf-picked."""
    query = (
        db.query(SKU)
        .options(joinedload(SKU.organization))
        .filter(SKU.product_type == "barcode", SKU.active.is_(True))
    )
    term = q.strip()
    if term:
        like = f"%{term}%"
        query = query.filter((SKU.name.ilike(like)) | (SKU.sku_code.ilike(like)) | (SKU.ean.ilike(like)))
    skus = query.order_by(SKU.name).limit(30).all()
    return [
        AvailableSKU(
            id=s.id,
            sku_code=s.sku_code,
            name=s.name,
            ean=s.ean,
            organization_name=s.organization.name if s.organization else None,
        )
        for s in skus
    ]
