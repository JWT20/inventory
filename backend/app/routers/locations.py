"""Pick-location management — courier-only (warehouse worker).

Products get a physical shelf location (row/cabinet/shelf) with a code printed
on the shelf. Barcode products are verified against it by scanning; loose
bottles cannot be scanned but do stand somewhere, so they are linked too and the
pick screen simply shows where to walk. Whole wine boxes stay out: they are
matched by photo per order and are never picked off a fixed shelf.

The whole router is gated to platform admin + courier via ``require_warehouse``;
owners/members never see it.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import require_warehouse
from app.database import get_db
from app.models import SKU, Location, SKULocation, User
from app.schemas import (
    AvailableSKU,
    LinkSKURequest,
    LocationBulkCreate,
    LocationBulkPreviewItem,
    LocationBulkResponse,
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


def _is_shelvable(sku: SKU) -> bool:
    """Whether this product has a fixed spot in the warehouse at all."""
    return sku.product_type == "barcode" or sku.is_bottle


def _to_response(location: Location) -> LocationResponse:
    skus = [
        LocationSKU(
            sku_id=link.sku_id,
            sku_code=link.sku.sku_code,
            name=link.sku.name,
            ean=link.sku.ean,
            organization_name=link.sku.organization.name if link.sku.organization else None,
            is_primary=link.is_primary,
            is_bottle=link.sku.is_bottle,
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


# How many generated codes the preview hands back. Enough to check the shape of
# the first and last aisle without shipping a thousand rows to a phone.
BULK_PREVIEW_LIMIT = 50


@router.post("/bulk", response_model=LocationBulkResponse, status_code=200)
def create_locations_bulk(data: LocationBulkCreate, db: Session = Depends(get_db)):
    """Create a whole rectangle of shelves in one go.

    Defaults to a dry run. The rectangle is easy to get wrong by an order of
    magnitude — two rows times five cabinets times shelves 0 to 100 is a
    thousand locations, not a hundred — and undoing that by hand is not a fix.
    So the first call answers with the count and a sample, and only an explicit
    ``dry_run: false`` writes anything.

    A code that already exists is skipped, not an error: topping up a
    half-filled aisle has to be repeatable.
    """
    # Dedupe rather than refuse: "B, B, C" is a typo in the input, not an
    # intent to make two of every shelf, and the template is not at fault for it.
    rijen = list(dict.fromkeys(value.strip() for value in data.rijen if value.strip()))
    kasten = list(dict.fromkeys(value.strip() for value in data.kasten if value.strip()))
    if not rijen or not kasten:
        raise HTTPException(400, "Vul minstens één rij en één kast in")

    planned: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for rij in rijen:
        for kast in kasten:
            for number in range(data.plank_van, data.plank_tot + 1):
                plank = str(number).zfill(data.plank_cijfers)
                code = (
                    data.code_template.replace("{rij}", rij)
                    .replace("{kast}", kast)
                    .replace("{plank}", plank)
                )
                if len(code) > 50:
                    raise HTTPException(
                        400, f"Code '{code}' is langer dan 50 tekens"
                    )
                # A template that leaves out a dimension collapses the whole
                # rectangle onto one code; refuse instead of silently creating
                # a single location from a thousand intended ones.
                if code in seen:
                    raise HTTPException(
                        400,
                        "Dit code-sjabloon levert dubbele codes op; gebruik "
                        "{rij}, {kast} én {plank}",
                    )
                seen.add(code)
                planned.append((code, rij, kast, plank))

    if len(planned) > 2000:
        raise HTTPException(
            400,
            f"Dit patroon levert {len(planned)} locaties op; beperk het bereik "
            "(maximaal 2000 per keer)",
        )

    existing = {
        row[0]
        for row in db.query(Location.code).filter(Location.code.in_(seen)).all()
    }

    preview = [
        LocationBulkPreviewItem(
            code=code, rij=rij, kast=kast, plank=plank, bestaat_al=code in existing
        )
        for code, rij, kast, plank in planned[:BULK_PREVIEW_LIMIT]
    ]
    to_create = [item for item in planned if item[0] not in existing]

    if data.dry_run:
        return LocationBulkResponse(
            dry_run=True,
            totaal=len(planned),
            aangemaakt=0,
            overgeslagen=len(planned) - len(to_create),
            voorbeeld=preview,
        )

    # Between the lookup above and this insert another bulk run may have claimed
    # some of the same codes. That is the same situation as a code that already
    # existed, so it is skipped the same way instead of failing the batch: one
    # savepoint per shelf, and a loser simply counts as "was already there".
    created = 0
    for code, rij, kast, plank in to_create:
        try:
            with db.begin_nested():
                db.add(Location(code=code, rij=rij, kast=kast, plank=plank))
        except IntegrityError:
            continue
        created += 1
    db.commit()
    logger.info("Bulk created %s pick locations", created)

    return LocationBulkResponse(
        dry_run=False,
        totaal=len(planned),
        aangemaakt=created,
        overgeslagen=len(planned) - created,
        voorbeeld=preview,
    )


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
    """Link a product to this location. Rejects whole wine boxes.

    A barcode product is verified by scanning the shelf code before its EANs. A
    loose bottle cannot be scanned, but it does stand somewhere, and telling the
    picker where is worth more than the consistency of only listing scannables.
    A wine box is matched by photo against the order it belongs to and never
    lives at a fixed spot, so linking one would only be misleading.
    """
    location = _load(db, location_id)
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "Product niet gevonden")
    if not _is_shelvable(sku):
        raise HTTPException(
            400, "Alleen barcode-producten en losse flessen kunnen een locatie krijgen"
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
    """Products the courier can link, across all merchants (courier has no org).

    Barcode products and loose bottles. Whole wine boxes are excluded — they are
    matched by photo per order and never picked off a fixed shelf.
    """
    query = (
        db.query(SKU)
        .options(joinedload(SKU.organization))
        .filter(
            or_(SKU.product_type == "barcode", SKU.is_bottle.is_(True)),
            SKU.active.is_(True),
        )
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
            is_bottle=s.is_bottle,
        )
        for s in skus
    ]
