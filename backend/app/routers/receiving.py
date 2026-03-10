import logging
import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, Booking, Order, OrderLine, ReferenceImage, User
from app.routers.skus import _sku_to_response
from app.schemas import BookingResponse, MatchResult, SKUResponse
from app.services.embedding import process_image
from app.services.matching import find_best_matches

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


def _read_image(file: UploadFile) -> bytes:
    """Read uploaded image bytes and reject files larger than 10 MB."""
    image_bytes = file.file.read()
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")
    return image_bytes


router = APIRouter(
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(get_current_user)]
)


@router.post("/identify", response_model=MatchResult | None)
def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Scan a box and identify it against reference images.

    Returns the matched SKU, or null if no match found.
    """
    image_bytes = _read_image(file)

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


@router.post("/book", response_model=BookingResponse)
def book_box(
    file: UploadFile,
    order_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """1 scan = 1 box = 1 booking.

    Scans the box, identifies the SKU, finds the matching order line,
    and creates a booking. Returns the rolcontainer assignment.
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")

    image_bytes = _read_image(file)

    # Save scan image
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)

    # Vision match
    description, embedding = process_image(image_bytes)
    candidates = find_best_matches(db, embedding, top_n=5)

    matched_sku, confidence = None, 0.0
    if candidates and candidates[0][1] >= settings.match_threshold:
        matched_sku, confidence = candidates[0]

    if matched_sku is None:
        raise HTTPException(
            404,
            "Doos niet herkend — geen match gevonden met referentiebeelden",
        )

    # Find matching order line
    order_line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == order_id,
            OrderLine.sku_id == matched_sku.id,
            OrderLine.booked_count < OrderLine.quantity,
        )
        .first()
    )
    if not order_line:
        raise HTTPException(
            400,
            f"SKU {matched_sku.sku_code} zit niet in deze order of is al volledig geboekt",
        )

    # Create booking
    booking = Booking(
        order_id=order_id,
        order_line_id=order_line.id,
        sku_id=matched_sku.id,
        scanned_by=user.id,
        scan_image_path=scan_path,
        confidence=confidence,
    )
    db.add(booking)
    order_line.booked_count += 1
    db.flush()

    # Check if order is fully booked
    all_booked = all(l.booked_count >= l.quantity for l in order.lines)
    if all_booked:
        order.status = "completed"

    db.commit()

    rolcontainer = f"KLANT {order_line.klant.upper()}"

    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": matched_sku.sku_code,
            "confidence": round(confidence, 4),
            "rolcontainer": rolcontainer,
            "klant": order_line.klant,
            "order_completed": all_booked,
        },
        user=user,
        resource_type="booking",
        resource_id=booking.id,
    )

    return BookingResponse(
        id=booking.id,
        order_id=order.id,
        order_reference=order.reference,
        sku_code=matched_sku.sku_code,
        sku_name=matched_sku.name,
        klant=order_line.klant,
        rolcontainer=rolcontainer,
        created_at=booking.created_at,
    )


@router.post("/new-product", response_model=SKUResponse)
def create_product_inline(
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

    image_bytes = _read_image(file)

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

    return _sku_to_response(sku)
