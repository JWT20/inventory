import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import User
from langfuse import observe, propagate_attributes, get_client as get_langfuse_client

from app.schemas import MatchResult
from app.services.embedding import process_image
from app.services.matching import find_best_matches

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/vision", tags=["vision"], dependencies=[Depends(get_current_user)]
)


@router.post("/identify", response_model=MatchResult | None)
@observe()
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Identify a box by image without order context.

    Useful for ad-hoc identification or testing.
    """
    with propagate_attributes(
        user_id=str(user.id),
        metadata={"endpoint": "/api/vision/identify", "username": user.username},
    ):
        image_bytes = file.file.read()
        if len(image_bytes) > 10 * 1024 * 1024:
            raise HTTPException(413, "Afbeelding te groot (max 10 MB)")
        try:
            description, embedding, is_package = await asyncio.to_thread(process_image, image_bytes)
        except Exception:
            logger.exception("Vision processing failed during ad-hoc identify")
            raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")

        if not is_package:
            publish_event(
                "vision_identify",
                details={
                    "matched_sku_code": None,
                    "confidence": None,
                    "vision_description": description,
                    "candidates": [],
                    "threshold": settings.match_threshold,
                    "rejected": True,
                    "rejection_reason": "not_a_package",
                },
                user=user,
                resource_type="vision",
            )
            # Enrich Langfuse trace for LLM-as-a-judge evaluation
            try:
                langfuse = get_langfuse_client()
                langfuse.update_current_observation(
                    metadata={
                        "vision_description": description,
                        "is_package": False,
                        "matched_sku_code": None,
                        "matched_sku_name": None,
                        "confidence": None,
                        "candidates": [],
                        "outcome": "rejected_not_a_package",
                    },
                )
            except Exception:
                pass
            return None

        candidates = find_best_matches(db, embedding, top_n=5)

        matched_sku, confidence = None, 0.0
        if candidates and candidates[0][1] >= settings.match_threshold:
            matched_sku, confidence = candidates[0]

        candidate_details = [
            {"sku_code": s.sku_code, "sku_name": s.name, "similarity": round(sim, 4)}
            for s, sim in candidates
        ]

        publish_event(
            "vision_identify",
            details={
                "matched_sku_code": matched_sku.sku_code if matched_sku else None,
                "confidence": round(confidence, 4) if matched_sku else None,
                "vision_description": description,
                "candidates": candidate_details,
                "threshold": settings.match_threshold,
            },
            user=user,
            resource_type="vision",
        )

        # Enrich Langfuse trace for LLM-as-a-judge evaluation
        try:
            langfuse = get_langfuse_client()
            langfuse.update_current_observation(
                metadata={
                    "vision_description": description,
                    "is_package": True,
                    "matched_sku_code": matched_sku.sku_code if matched_sku else None,
                    "matched_sku_name": matched_sku.name if matched_sku else None,
                    "confidence": round(confidence, 4) if matched_sku else None,
                    "candidates": candidate_details,
                    "outcome": "matched" if matched_sku else "no_match",
                },
            )
        except Exception:
            pass

        if matched_sku is None:
            return None

        return MatchResult(
            sku_id=matched_sku.id,
            sku_code=matched_sku.sku_code,
            sku_name=matched_sku.name,
            confidence=confidence,
        )
