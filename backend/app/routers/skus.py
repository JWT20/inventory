import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, require_admin, require_product_manager
from app.config import settings
from app.database import get_db, SessionLocal
from app.events import publish_event
from app.models import SKU, ReferenceImage, User
from app.schemas import (
    ReferenceImageResponse,
    SKUCreate,
    SKUResponse,
    SKUUpdate,
    generate_display_name,
    generate_sku_code,
)
from app.services.embedding import describe_image, process_image

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skus", tags=["skus"])


def _sku_to_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        producent=sku.producent,
        wijnaam=sku.wijnaam,
        wijntype=sku.wijntype,
        jaargang=sku.jaargang,
        volume=sku.volume,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )


@router.get("", response_model=list[SKUResponse])
def list_skus(
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(SKU).options(joinedload(SKU.reference_images))
    if active_only:
        query = query.filter(SKU.active.is_(True))
    skus = query.order_by(SKU.name).all()
    return [_sku_to_response(s) for s in skus]


@router.post("", response_model=SKUResponse, status_code=201)
def create_sku(
    data: SKUCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku_code = generate_sku_code(data.producent, data.wijnaam, data.wijntype, data.jaargang, data.volume)
    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' bestaat al")
    name = generate_display_name(data.producent, data.wijnaam, data.wijntype, data.jaargang)
    description = f"{data.producent} {data.wijnaam} {data.wijntype} {data.jaargang} {data.volume}"
    sku = SKU(
        sku_code=sku_code,
        name=name,
        description=description,
        active=data.active,
        producent=data.producent,
        wijnaam=data.wijnaam,
        wijntype=data.wijntype,
        jaargang=data.jaargang,
        volume=data.volume,
    )
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
    _: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
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
    for field, value in changed_fields.items():
        setattr(sku, field, value)

    # Regenerate sku_code and name if wine fields are all present
    wine_fields = ("producent", "wijnaam", "wijntype", "jaargang", "volume")
    if any(f in changed_fields for f in wine_fields) and all(getattr(sku, f) for f in wine_fields):
        new_code = generate_sku_code(sku.producent, sku.wijnaam, sku.wijntype, sku.jaargang, sku.volume)
        conflict = db.query(SKU).filter(SKU.sku_code == new_code, SKU.id != sku_id).first()
        if conflict:
            raise HTTPException(400, f"SKU code '{new_code}' bestaat al")
        sku.sku_code = new_code
        sku.name = generate_display_name(sku.producent, sku.wijnaam, sku.wijntype, sku.jaargang)

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


def _process_reference_image_background(image_id: int, image_path: str, sku_code: str) -> None:
    """Background task: run vision + embedding and update the DB record."""
    db = SessionLocal()
    try:
        ref_image = db.get(ReferenceImage, image_id)
        if not ref_image:
            logger.error("Reference image %d not found for background processing", image_id)
            return
        ref_image.processing_status = "processing"
        db.commit()

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        description, embedding, is_wine = process_image(image_bytes)
        ref_image.vision_description = description
        if not is_wine:
            ref_image.processing_status = "failed"
            db.commit()
            logger.warning("Background processing rejected non-wine image %d (SKU %s)", image_id, sku_code)
            return
        ref_image.embedding = embedding
        ref_image.processing_status = "done"
        db.commit()
        logger.info("Background processing complete for reference image %d (SKU %s)", image_id, sku_code)
    except Exception:
        logger.exception("Background processing failed for reference image %d (SKU %s)", image_id, sku_code)
        try:
            ref_image = db.get(ReferenceImage, image_id)
            if ref_image:
                ref_image.processing_status = "failed"
                db.commit()
        except Exception:
            logger.exception("Failed to mark reference image %d as failed", image_id)
    finally:
        db.close()


@router.post("/{sku_id}/images", response_model=ReferenceImageResponse, status_code=201)
def upload_reference_image(
    sku_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    skip_wine_check: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    image_bytes = file.file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")

    # Synchronous wine classification check before saving (can be overridden)
    if not skip_wine_check:
        _description, is_wine = describe_image(image_bytes)
        if not is_wine:
            raise HTTPException(400, "Dit is geen wijndoos — upload alleen foto's van wijndozen")

    # Save image to disk
    ref_dir = os.path.join(settings.upload_dir, "reference_images", str(sku_id))
    os.makedirs(ref_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    image_path = os.path.join(ref_dir, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # Create DB record immediately with pending status
    ref_image = ReferenceImage(
        sku_id=sku_id,
        image_path=image_path,
        processing_status="pending",
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

    # Process vision + embedding in background
    background_tasks.add_task(
        _process_reference_image_background, ref_image.id, image_path, sku.sku_code,
    )

    return ref_image


@router.get("/{sku_id}/images", response_model=list[ReferenceImageResponse])
def list_reference_images(
    sku_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
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
    if os.path.exists(image.image_path):
        os.remove(image.image_path)
    db.delete(image)
    db.commit()
    publish_event(
        "reference_image_deleted",
        details={"sku_code": sku.sku_code if sku else None, "image_id": image_id},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )
