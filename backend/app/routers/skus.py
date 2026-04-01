import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_admin, require_product_manager
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import (
    SKU,
    Booking,
    InboundShipmentLine,
    InventoryBalance,
    OrderLine,
    ReferenceImage,
    StockMovement,
    User,
)
from app.schemas import (
    WINE_ATTRIBUTE_KEYS,
    ReferenceImageResponse,
    SKUCreate,
    SKUResponse,
    SKUUpdate,
    generate_wine_display_name,
    generate_wine_sku_code,
)
from langfuse import observe

from app.services.embedding import (
    assess_description_quality,
    classify_and_describe,
    describe_and_embed,
    generate_embedding,
)
from app.services.storage import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skus", tags=["skus"])

# Threshold for considering two images as duplicates (cosine similarity)
DUPLICATE_SIMILARITY_THRESHOLD = 0.90


def _check_duplicate_embedding(
    db: Session, embedding: list[float], exclude_sku_id: int,
) -> tuple[SKU | None, float]:
    """Check if a similar image already exists on a different SKU.

    Returns (matching_sku, similarity) or (None, 0.0).
    """
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    row = db.execute(
        text("""
            SELECT ri.sku_id, 1 - (ri.embedding <=> :embedding) AS similarity
            FROM reference_images ri
            WHERE ri.embedding IS NOT NULL
              AND ri.sku_id != :exclude_sku_id
            ORDER BY ri.embedding <=> :embedding
            LIMIT 1
        """),
        {"embedding": embedding_str, "exclude_sku_id": exclude_sku_id},
    ).first()
    if row and row[1] >= DUPLICATE_SIMILARITY_THRESHOLD:
        sku = db.get(SKU, row[0])
        return sku, float(row[1])
    return None, 0.0


def _sku_to_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        category=sku.category,
        attributes=sku.attributes_dict,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )


@router.get("", response_model=list[SKUResponse])
def list_skus(
    active_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(SKU).options(selectinload(SKU.reference_images), selectinload(SKU.attributes))
    if active_only:
        query = query.filter(SKU.active.is_(True))
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(SKU.organization_id == user.organization_id)
        else:
            # Users without an org (e.g. couriers) must not see all SKUs
            query = query.filter(SKU.organization_id.is_(None))
    skus = query.order_by(SKU.name).offset(offset).limit(limit).all()
    return [_sku_to_response(s) for s in skus]


@router.post("", response_model=SKUResponse, status_code=201)
def create_sku(
    data: SKUCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    # For wine: auto-generate sku_code and name from attributes
    if data.category == "wine":
        sku_code = data.sku_code or generate_wine_sku_code(data.attributes)
        name = data.name or generate_wine_display_name(data.attributes)
        description = " ".join(data.attributes.get(k, "") for k in WINE_ATTRIBUTE_KEYS)
    else:
        if not data.sku_code:
            raise HTTPException(400, "sku_code is verplicht voor niet-wijn producten")
        if not data.name:
            raise HTTPException(400, "name is verplicht voor niet-wijn producten")
        sku_code = data.sku_code
        name = data.name
        description = name

    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' bestaat al")

    sku = SKU(
        sku_code=sku_code,
        name=name,
        description=description,
        active=data.active,
        category=data.category,
        organization_id=user.organization_id,
    )
    sku.set_attributes(data.attributes)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    publish_event(
        "sku_created",
        details={"sku_code": sku.sku_code, "name": sku.name},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )
    return _sku_to_response(sku)


@router.get("/{sku_id}", response_model=SKUResponse)
def get_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin:
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")
    return _sku_to_response(sku)


@router.patch("/{sku_id}", response_model=SKUResponse)
def update_sku(
    sku_id: int,
    data: SKUUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    changed_fields = data.model_dump(exclude_unset=True)

    if data.active is not None:
        sku.active = data.active

    if data.attributes is not None:
        sku.set_attributes(data.attributes)
        # Regenerate sku_code and name for wine SKUs when attributes change
        if sku.category == "wine":
            attrs = sku.attributes_dict
            if all(attrs.get(k) for k in WINE_ATTRIBUTE_KEYS):
                new_code = generate_wine_sku_code(attrs)
                conflict = db.query(SKU).filter(SKU.sku_code == new_code, SKU.id != sku_id).first()
                if conflict:
                    raise HTTPException(400, f"SKU code '{new_code}' bestaat al")
                sku.sku_code = new_code
                sku.name = generate_wine_display_name(attrs)

    db.commit()
    db.refresh(sku)
    publish_event(
        "sku_updated",
        details={"sku_code": sku.sku_code, "changed_fields": list(changed_fields.keys())},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )
    return _sku_to_response(sku)


@router.delete("/{sku_id}", status_code=204)
def delete_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    blockers: list[str] = []
    if db.query(OrderLine).filter(OrderLine.sku_id == sku_id).first():
        blockers.append("order lines")
    if db.query(Booking).filter(Booking.sku_id == sku_id).first():
        blockers.append("bookings")
    if db.query(InboundShipmentLine).filter(InboundShipmentLine.sku_id == sku_id).first():
        blockers.append("inbound shipment lines")
    if db.query(StockMovement).filter(StockMovement.sku_id == sku_id).first():
        blockers.append("stock movements")
    if db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku_id).first():
        blockers.append("inventory balance")
    if blockers:
        raise HTTPException(
            409,
            f"Cannot delete SKU '{sku.sku_code}': still referenced by {', '.join(blockers)}",
        )

    sku_code = sku.sku_code
    db.delete(sku)
    db.commit()
    publish_event(
        "sku_deleted",
        details={"sku_code": sku_code},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )


@router.post("/{sku_id}/images", response_model=ReferenceImageResponse, status_code=201)
@observe()
async def upload_reference_image(
    sku_id: int,
    file: UploadFile,
    skip_wine_check: bool = Form(False),
    skip_duplicate_check: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    image_bytes = file.file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")

    # Classify + describe + embed, then check for duplicates
    if skip_wine_check:
        description, embedding, quality = await describe_and_embed(image_bytes)
    else:
        is_package, description = await classify_and_describe(image_bytes)
        if not is_package:
            raise HTTPException(400, f"Dit is geen doos of verpakking ({description}) — upload alleen foto's van dozen")
        quality = assess_description_quality(description)
        embedding = await generate_embedding(description)

    # Duplicate detection via embedding similarity
    if not skip_duplicate_check:
        dup_sku, similarity = _check_duplicate_embedding(db, embedding, exclude_sku_id=sku_id)
        if dup_sku:
            raise HTTPException(
                409,
                f"Deze foto lijkt te veel op een foto van {dup_sku.sku_code} (gelijkenis: {similarity:.0%})",
            )

    # Save image
    image_key = f"reference_images/{sku_id}/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)

    # Create DB record with description + embedding already filled
    ref_image = ReferenceImage(
        sku_id=sku_id,
        image_path=image_key,
        vision_description=description,
        embedding=embedding,
        description_quality=quality,
        processing_status="done",
        wine_check_overridden=skip_wine_check,
    )
    db.add(ref_image)
    db.commit()
    db.refresh(ref_image)

    publish_event(
        "reference_image_uploaded",
        details={"sku_code": sku.sku_code, "image_id": ref_image.id},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )

    return ref_image


@router.get("/{sku_id}/images", response_model=list[ReferenceImageResponse])
def list_reference_images(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin:
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")
    return sku.reference_images


@router.delete("/{sku_id}/images/{image_id}", status_code=204)
def delete_reference_image(
    sku_id: int,
    image_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    image = (
        db.query(ReferenceImage)
        .filter(ReferenceImage.id == image_id, ReferenceImage.sku_id == sku_id)
        .first()
    )
    if not image:
        raise HTTPException(404, "Reference image not found")
    sku = db.get(SKU, sku_id)
    storage.delete(image.image_path)
    db.delete(image)
    db.commit()
    publish_event(
        "reference_image_deleted",
        details={"sku_code": sku.sku_code if sku else None, "image_id": image_id},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )
