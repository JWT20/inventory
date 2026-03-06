import logging
import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, ReferenceImage, User
from app.schemas import DockAssignment, ReceiveResult, SKUResponse
from app.services.allocation import confirm_receipt, find_allocation
from app.services.embedding import process_image
from app.services.matching import find_best_match, find_best_matches

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(get_current_user)]
)


@router.post("/identify", response_model=ReceiveResult | None)
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Scan a box, identify it, and find a cross-docking assignment."""
    image_bytes = await file.read()

    # Save scan image
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)

    description, embedding = process_image(image_bytes)
    matched_sku, confidence = find_best_match(db, embedding)
    candidates = find_best_matches(db, embedding, top_n=5)

    # Cross-docking: find order allocation
    assignment = None
    if matched_sku:
        alloc = find_allocation(db, matched_sku.id)
        if alloc:
            line, order = alloc
            assignment = DockAssignment(
                order_id=order.id,
                order_number=order.order_number,
                customer_name=order.customer_name,
                dock_location=order.dock_location,
                line_id=line.id,
                quantity_needed=line.quantity - line.received_quantity,
                quantity_after=line.received_quantity + 1,
            )

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
            "has_order": assignment is not None,
            "dock_location": assignment.dock_location if assignment else None,
        },
        user=user,
        resource_type="receiving",
    )

    if matched_sku is None:
        return None

    return ReceiveResult(
        sku_id=matched_sku.id,
        sku_code=matched_sku.sku_code,
        sku_name=matched_sku.name,
        confidence=confidence,
        assignment=assignment,
    )


class ConfirmRequest(BaseModel):
    line_id: int


@router.post("/confirm")
def confirm_receive(
    data: ConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confirm a box has been placed on the correct dock location."""
    try:
        line, order = confirm_receipt(db, data.line_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    publish_event(
        "box_received",
        details={
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "dock_location": order.dock_location,
            "sku_code": line.sku.sku_code,
            "received_quantity": line.received_quantity,
            "quantity": line.quantity,
            "line_status": line.status,
            "order_status": order.status,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    if line.status == "fulfilled":
        publish_event(
            "order_line_fulfilled",
            details={
                "order_number": order.order_number,
                "sku_code": line.sku.sku_code,
            },
            user=user,
            resource_type="order",
            resource_id=order.id,
        )

    if order.status == "fulfilled":
        publish_event(
            "order_fulfilled",
            details={
                "order_number": order.order_number,
                "customer_name": order.customer_name,
                "dock_location": order.dock_location,
            },
            user=user,
            resource_type="order",
            resource_id=order.id,
        )

    return {
        "line_status": line.status,
        "order_status": order.status,
        "received_quantity": line.received_quantity,
        "quantity": line.quantity,
    }


@router.post("/new-product", response_model=SKUResponse)
async def create_product_inline(
    file: UploadFile,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Quick-create a new SKU with a reference image from the camera."""
    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' already exists")

    sku = SKU(sku_code=sku_code, name=name, description=description)
    db.add(sku)
    db.flush()

    image_bytes = await file.read()

    ref_dir = os.path.join(settings.upload_dir, "reference_images", str(sku.id))
    os.makedirs(ref_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    image_path = os.path.join(ref_dir, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

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

    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )
