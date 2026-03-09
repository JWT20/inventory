import logging
import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, ReferenceImage, User
from app.services.sku_utils import sku_to_response
from app.schemas import MatchResult, SKUResponse
from app.services.embedding import process_image
from app.services.matching import find_best_matches

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(get_current_user)]
)


@router.post("/identify", response_model=MatchResult | None)
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Scan a box and identify it against reference images.

    Returns the matched SKU, or null if no match found.
    """
    image_bytes = await file.read()

    # Save scan image for later reference
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)

    description, embedding = process_image(image_bytes)
    candidates = find_best_matches(db, embedding, top_n=5)

    matched_sku, confidence = None, 0.0
    if candidates and candidates[0][1] >= settings.match_threshold:
        matched_sku, confidence = candidates[0]

    publish_event(
        "box_identified",
        details={
            "matched_sku_code": matched_sku.sku_code if matched_sku else None,
            "confidence": round(confidence, 4) if matched_sku else None,
            "vision_description": description,
            "candidates": [
                {"sku_code": s.sku_code, "sku_name": s.name, "similarity": round(sim, 4)}
                for s, sim in candidates
            ],
            "threshold": settings.match_threshold,
        },
        user=user,
        resource_type="receiving",
    )

    if matched_sku is None:
        return None

    return MatchResult(
        sku_id=matched_sku.id,
        sku_code=matched_sku.sku_code,
        sku_name=matched_sku.name,
        confidence=confidence,
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

    publish_event(
        "product_created_inline",
        details={"sku_code": sku.sku_code, "name": sku.name},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )

    return sku_to_response(sku)
