import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import OrderLine, PickLog
from app.schemas import PickResult
from app.services.embedding import generate_embedding
from app.services.matching import find_best_match

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/picks", tags=["picks"])


@router.post("/validate/{order_line_id}", response_model=PickResult)
async def validate_pick(
    order_line_id: int, file: UploadFile, db: Session = Depends(get_db)
):
    """Validate a pick by scanning a wine box and comparing to expected SKU."""
    order_line = db.get(OrderLine, order_line_id)
    if not order_line:
        raise HTTPException(404, "Order line not found")

    expected_sku = order_line.sku
    image_bytes = await file.read()

    # Save scan image
    scan_dir = os.path.join(settings.upload_dir, "scans")
    os.makedirs(scan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    scan_path = os.path.join(scan_dir, filename)
    with open(scan_path, "wb") as f:
        f.write(image_bytes)

    # Generate embedding and find match
    embedding = generate_embedding(image_bytes)
    matched_sku, confidence = find_best_match(db, embedding)

    correct = matched_sku is not None and matched_sku.id == expected_sku.id

    # Log the pick attempt
    pick_log = PickLog(
        order_line_id=order_line_id,
        matched_sku_id=matched_sku.id if matched_sku else None,
        confidence=confidence,
        correct=correct,
        image_path=scan_path,
    )
    db.add(pick_log)

    # Update picked quantity if correct
    if correct:
        order_line.picked_quantity = min(
            order_line.picked_quantity + 1, order_line.quantity
        )
        if order_line.picked_quantity >= order_line.quantity:
            order_line.status = "picked"

        # Check if all lines are picked
        order = order_line.order
        all_picked = all(l.status == "picked" for l in order.lines)
        if all_picked:
            order.status = "completed"

    db.commit()

    if correct:
        message = f"Correct! Dit is {expected_sku.name}"
    elif matched_sku:
        message = (
            f"Verkeerd! Dit lijkt op {matched_sku.name}, "
            f"maar je zoekt {expected_sku.name}"
        )
    else:
        message = f"Niet herkend. Je zoekt {expected_sku.name}"

    return PickResult(
        correct=correct,
        confidence=confidence,
        matched_sku_code=matched_sku.sku_code if matched_sku else None,
        matched_sku_name=matched_sku.name if matched_sku else None,
        expected_sku_code=expected_sku.sku_code,
        expected_sku_name=expected_sku.name,
        message=message,
    )
