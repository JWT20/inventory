import logging

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from app.auth import require_picker
from app.database import get_db
from app.models import User
from app.schemas import MatchResult
from app.services.embedding import process_image
from app.services.matching import find_best_match

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vision", tags=["vision"])


@router.post("/identify", response_model=MatchResult | None)
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    _user: User = Depends(require_picker),
):
    """Identify a wine box by image without order context.

    Useful for ad-hoc identification or testing.
    """
    image_bytes = await file.read()
    _description, embedding = process_image(image_bytes)
    matched_sku, confidence = find_best_match(db, embedding)

    if matched_sku is None:
        return None

    return MatchResult(
        sku_id=matched_sku.id,
        sku_code=matched_sku.sku_code,
        sku_name=matched_sku.name,
        confidence=confidence,
    )
