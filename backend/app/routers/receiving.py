import logging
import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import SKU, ReferenceImage, StockMovement, User
from app.schemas import (
    MatchResult,
    ReceiveConfirm,
    SKUCreate,
    SKUResponse,
    StockMovementResponse,
)
from app.services.embedding import process_image
from app.services.matching import find_best_match

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(get_current_user)]
)


@router.post("/identify", response_model=MatchResult | None)
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
):
    """Scan a box and identify it against reference images.

    Returns the matched SKU with stock info, or null if no match found.
    """
    image_bytes = await file.read()

    # Save scan image for later reference
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)

    _description, embedding = process_image(image_bytes)
    matched_sku, confidence = find_best_match(db, embedding)

    if matched_sku is None:
        return None

    return MatchResult(
        sku_id=matched_sku.id,
        sku_code=matched_sku.sku_code,
        sku_name=matched_sku.name,
        stock_quantity=matched_sku.stock_quantity,
        confidence=confidence,
    )


@router.post("/confirm", response_model=StockMovementResponse)
def confirm_receiving(
    data: ReceiveConfirm,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confirm receiving: update stock and log the movement."""
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    sku.stock_quantity += data.quantity

    movement = StockMovement(
        sku_id=sku.id,
        user_id=user.id,
        quantity=data.quantity,
        movement_type="received",
        confidence=data.confidence,
        scan_image_path=data.scan_image_path,
        notes=data.notes,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)

    logger.info(
        "Received %d x %s (stock now %d) by %s",
        data.quantity,
        sku.sku_code,
        sku.stock_quantity,
        user.username,
    )

    return StockMovementResponse(
        id=movement.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        quantity=movement.quantity,
        movement_type=movement.movement_type,
        confidence=movement.confidence,
        notes=movement.notes,
        username=user.username,
        created_at=movement.created_at,
    )


@router.post("/new-product", response_model=SKUResponse)
async def create_product_inline(
    file: UploadFile,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Quick-create a new SKU with a reference image from the camera.

    Used when a scanned box is not recognized.
    """
    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' already exists")

    sku = SKU(sku_code=sku_code, name=name, description=description)
    db.add(sku)
    db.flush()

    image_bytes = await file.read()

    # Save reference image
    ref_dir = os.path.join(settings.upload_dir, "reference_images", str(sku.id))
    os.makedirs(ref_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    image_path = os.path.join(ref_dir, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # Process with Vision API
    logger.info("Processing reference image for new SKU %s", sku_code)
    vision_description, embedding = process_image(image_bytes)

    ref_image = ReferenceImage(
        sku_id=sku.id,
        image_path=image_path,
        vision_description=vision_description,
        embedding=embedding,
    )
    db.add(ref_image)
    db.commit()
    db.refresh(sku)

    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        stock_quantity=sku.stock_quantity,
        active=sku.active,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )


@router.get("/history", response_model=list[StockMovementResponse])
def list_movements(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List recent stock movements."""
    movements = (
        db.query(StockMovement)
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        StockMovementResponse(
            id=m.id,
            sku_id=m.sku_id,
            sku_code=m.sku.sku_code,
            sku_name=m.sku.name,
            quantity=m.quantity,
            movement_type=m.movement_type,
            confidence=m.confidence,
            notes=m.notes,
            username=m.user.username,
            created_at=m.created_at,
        )
        for m in movements
    ]
