import asyncio
import logging
import os
import time
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import require_warehouse
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, Booking, Order, OrderLine, ReferenceImage, User
from app.routers.skus import _sku_to_response, compute_image_hash
from app.schemas import AlternativeMatch, BookingConfirmation, BookingResponse, ConfirmBookingRequest, MatchResult, SKUResponse
from langfuse import observe, propagate_attributes

from app.services.embedding import assess_description_quality, process_image
from app.services.matching import find_best_matches

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
CONFIRMATION_TOKEN_MAX_AGE = 120  # seconds

_signer = URLSafeTimedSerializer(settings.secret_key, salt="booking-confirm")


def _scan_url(scan_path: str) -> str:
    """Convert an absolute file path to a URL served via /api/uploads/."""
    rel = os.path.relpath(scan_path, settings.upload_dir)
    return f"/api/uploads/{rel}"


def _reference_image_url(image_path: str | None) -> str:
    """Convert a reference image file path to a URL served via /api/uploads/."""
    if image_path:
        return _scan_url(image_path)
    return ""


def _read_image(file: UploadFile) -> bytes:
    """Read uploaded image bytes and reject files larger than 10 MB."""
    image_bytes = file.file.read()
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")
    return image_bytes


router = APIRouter(
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(require_warehouse)]
)


@router.post("/identify", response_model=MatchResult | None)
@observe()
async def identify_box(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Scan a box and identify it against reference images.

    Returns the matched SKU, or null if no match found.
    """
    with propagate_attributes(
        user_id=str(user.id),
        metadata={"endpoint": "/api/receiving/identify", "username": user.username},
    ):
        t_start = time.perf_counter()

        image_bytes = _read_image(file)
        t_read = time.perf_counter()

        # Save scan image for later reference
        scan_dir = os.path.join(settings.upload_dir, "scans")
        os.makedirs(scan_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.jpg"
        scan_path = os.path.join(scan_dir, filename)
        with open(scan_path, "wb") as f:
            f.write(image_bytes)
        t_save = time.perf_counter()

        try:
            description, embedding, is_package = await asyncio.to_thread(process_image, image_bytes)
        except Exception:
            logger.exception("Vision processing failed during identify")
            raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")
        t_process = time.perf_counter()

        if not is_package:
            logger.info(
                "[TIMING] identify total=%.0fms (rejected: not a package) | read=%.0fms save=%.0fms process_image=%.0fms",
                (t_process - t_start) * 1000,
                (t_read - t_start) * 1000,
                (t_save - t_read) * 1000,
                (t_process - t_save) * 1000,
            )
            publish_event(
                "box_identified",
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
                resource_type="receiving",
            )
            return None

        candidates = find_best_matches(db, embedding, top_n=5)
        t_match = time.perf_counter()

        matched_sku, confidence, matched_ref_desc = None, 0.0, None
        if candidates and candidates[0][1] >= settings.match_threshold:
            matched_sku, confidence = candidates[0][0], candidates[0][1]
            matched_ref_desc = candidates[0][3]

        logger.info(
            "[TIMING] identify total=%.0fms | read=%.0fms save=%.0fms process_image=%.0fms matching=%.0fms",
            (t_match - t_start) * 1000,
            (t_read - t_start) * 1000,
            (t_save - t_read) * 1000,
            (t_process - t_save) * 1000,
            (t_match - t_process) * 1000,
        )

        candidate_details = [
            {
                "sku_code": s.sku_code,
                "sku_name": s.name,
                "similarity": round(sim, 4),
                "reference_description": ref_desc,
            }
            for s, sim, _img_path, ref_desc in candidates
        ]

        publish_event(
            "box_identified",
            details={
                "matched_sku_code": matched_sku.sku_code if matched_sku else None,
                "confidence": round(confidence, 4) if matched_sku else None,
                "vision_description": description,
                "candidates": candidate_details,
                "threshold": settings.match_threshold,
            },
            user=user,
            resource_type="receiving",
        )

        if matched_sku is None:
            return None

        # Flag for human confirmation if description quality is low, confidence is low,
        # or there are close rival matches (ambiguity).
        CONFIRM_THRESHOLD = 0.84
        quality = assess_description_quality(description)
        reasons = []
        alternatives: list[AlternativeMatch] = []

        if quality == "low":
            reasons.append("low-quality description")
        if confidence < CONFIRM_THRESHOLD:
            reasons.append(f"low confidence ({confidence:.2f} < {CONFIRM_THRESHOLD})")

        # Check for ambiguous matches: if #2 is within ambiguity_margin of #1
        if len(candidates) >= 2:
            gap = candidates[0][1] - candidates[1][1]
            if gap < settings.ambiguity_margin:
                rival = candidates[1]
                reasons.append(
                    f"ambiguous match ({rival[0].sku_code} at {rival[1]:.3f} is only {gap:.3f} away)"
                )
                # Include all close rivals as alternatives
                for s, sim, img_path, _ref_desc in candidates[1:]:
                    if candidates[0][1] - sim < settings.ambiguity_margin:
                        alternatives.append(AlternativeMatch(
                            sku_id=s.id,
                            sku_code=s.sku_code,
                            sku_name=s.name,
                            confidence=sim,
                            reference_image_url=_reference_image_url(img_path),
                        ))

        needs_confirmation = len(reasons) > 0
        confirmation_reason = ", ".join(reasons) if reasons else None
        if needs_confirmation:
            logger.info(
                "Identify: SKU %s flagged for confirmation: %s",
                matched_sku.sku_code, confirmation_reason,
            )

        return MatchResult(
            sku_id=matched_sku.id,
            sku_code=matched_sku.sku_code,
            sku_name=matched_sku.name,
            confidence=confidence,
            needs_confirmation=needs_confirmation,
            confirmation_reason=confirmation_reason,
            alternatives=alternatives,
        )


@router.post("/book", response_model=BookingResponse | BookingConfirmation)
@observe()
async def book_box(
    file: UploadFile,
    order_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """1 scan = 1 box = 1 booking.

    Scans the box, identifies the SKU, finds the matching order line,
    and creates a booking. Returns the rolcontainer assignment.
    """
    with propagate_attributes(
        user_id=str(user.id),
        session_id=str(order_id),
        metadata={"endpoint": "/api/receiving/book", "username": user.username},
    ):
        t_start = time.perf_counter()

        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "Order niet gevonden")
        if order.status != "active":
            raise HTTPException(400, f"Order is niet actief (status: {order.status})")

        image_bytes = _read_image(file)
        t_read = time.perf_counter()

        # Save scan image
        scan_dir = os.path.join(settings.upload_dir, "scans")
        os.makedirs(scan_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.jpg"
        scan_path = os.path.join(scan_dir, filename)
        with open(scan_path, "wb") as f:
            f.write(image_bytes)
        t_save = time.perf_counter()

        # Vision: classify + describe + embed
        try:
            description, embedding, is_package = await asyncio.to_thread(process_image, image_bytes)
        except Exception:
            logger.exception("Vision processing failed during booking")
            raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")
        t_process = time.perf_counter()

        if not is_package:
            publish_event(
                "box_booked",
                details={
                    "order_reference": order.reference,
                    "rejected": True,
                    "rejection_reason": "not_a_package",
                    "vision_description": description,
                },
                user=user,
                resource_type="booking",
            )
            raise HTTPException(
                422,
                "Dit is geen doos of verpakking — scan een productdoos",
            )

        # Only match against SKUs in this order
        order_sku_ids = [line.sku_id for line in order.lines]
        candidates = find_best_matches(db, embedding, top_n=5, sku_ids=order_sku_ids)
        t_match = time.perf_counter()

        matched_sku, confidence, matched_image_path, matched_ref_desc = None, 0.0, None, None
        if candidates and candidates[0][1] >= settings.match_threshold:
            matched_sku, confidence, matched_image_path, matched_ref_desc = candidates[0]

        # Always run an unrestricted search to detect cross-order ambiguity
        all_candidates = find_best_matches(db, embedding, top_n=5)

        if matched_sku is None:
            # Check if the box matches a SKU outside this order
            if all_candidates and all_candidates[0][1] >= settings.match_threshold:
                wrong_sku = all_candidates[0][0]
                raise HTTPException(
                    409,
                    f"Deze doos lijkt op SKU {wrong_sku.sku_code} ({wrong_sku.name}), "
                    f"maar die zit niet in deze order",
                )
            raise HTTPException(
                404,
                "Doos niet herkend — geen match gevonden met SKUs in deze order",
            )

        # Gate: require human confirmation if description quality is low,
        # confidence is low, or there are close rival matches (ambiguity).
        CONFIRM_THRESHOLD = 0.84
        quality = assess_description_quality(description)
        reason: list[str] = []
        alternatives: list[AlternativeMatch] = []

        if quality == "low":
            reason.append("low-quality description")
        if confidence < CONFIRM_THRESHOLD:
            reason.append(f"low confidence ({confidence:.2f} < {CONFIRM_THRESHOLD})")

        # Check for ambiguous matches within the order candidates
        if len(candidates) >= 2:
            gap = candidates[0][1] - candidates[1][1]
            if gap < settings.ambiguity_margin:
                rival = candidates[1]
                reason.append(
                    f"ambiguous match ({rival[0].sku_code} at {rival[1]:.3f} is only {gap:.3f} away)"
                )
                for s, sim, img_path, _ref_desc in candidates[1:]:
                    if candidates[0][1] - sim < settings.ambiguity_margin:
                        alternatives.append(AlternativeMatch(
                            sku_id=s.id,
                            sku_code=s.sku_code,
                            sku_name=s.name,
                            confidence=sim,
                            reference_image_url=_reference_image_url(img_path),
                        ))

        # Cross-check: if the unrestricted catalog has a better or close match
        # with a *different* SKU, flag ambiguity even when the order has only one SKU.
        for s, sim, img_path, _ref_desc in all_candidates:
            if s.id == matched_sku.id:
                continue
            if sim >= confidence - settings.ambiguity_margin:
                if not any(a.sku_id == s.id for a in alternatives):
                    reason.append(
                        f"better match outside order ({s.sku_code} at {sim:.3f} vs order match at {confidence:.3f})"
                    )
                    alternatives.append(AlternativeMatch(
                        sku_id=s.id,
                        sku_code=s.sku_code,
                        sku_name=s.name,
                        confidence=sim,
                        reference_image_url=_reference_image_url(img_path),
                    ))

        needs_confirmation = len(reason) > 0
        if needs_confirmation:
            logger.info(
                "SKU %s flagged for confirmation: %s",
                matched_sku.sku_code, ", ".join(reason),
            )
            token_data = {
                "order_id": order_id,
                "sku_id": matched_sku.id,
                "confidence": round(confidence, 4),
                "scan_image_path": scan_path,
                "user_id": user.id,
            }
            token = _signer.dumps(token_data)
            return BookingConfirmation(
                confirmation_token=token,
                sku_code=matched_sku.sku_code,
                sku_name=matched_sku.name,
                confidence=confidence,
                scan_image_url=_scan_url(scan_path),
                reference_image_url=_reference_image_url(matched_image_path),
                alternatives=alternatives,
            )

        # Find matching order line with remaining quantity
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
                f"SKU {matched_sku.sku_code} is al volledig geboekt in deze order",
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
        t_booking = time.perf_counter()

        rolcontainer = f"KLANT {order_line.customer_name.upper()}"

        logger.info(
            "[TIMING] book total=%.0fms | read=%.0fms save=%.0fms process_image=%.0fms matching=%.0fms booking=%.0fms",
            (t_booking - t_start) * 1000,
            (t_read - t_start) * 1000,
            (t_save - t_read) * 1000,
            (t_process - t_save) * 1000,
            (t_match - t_process) * 1000,
            (t_booking - t_match) * 1000,
        )

        publish_event(
            "box_booked",
            details={
                "order_reference": order.reference,
                "sku_code": matched_sku.sku_code,
                "confidence": round(confidence, 4),
                "rolcontainer": rolcontainer,
                "klant": order_line.customer_name,
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
            klant=order_line.customer_name,
            rolcontainer=rolcontainer,
            created_at=booking.created_at,
        )


@router.post("/book/confirm", response_model=BookingResponse)
@observe()
def confirm_booking(
    body: ConfirmBookingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Confirm a booking that was flagged for human approval (low-quality description)."""
    try:
        data = _signer.loads(body.confirmation_token, max_age=CONFIRMATION_TOKEN_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(410, "Bevestigingstoken verlopen — scan opnieuw")
    except BadSignature:
        raise HTTPException(400, "Ongeldig bevestigingstoken")

    order = db.get(Order, data["order_id"])
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")

    sku = db.get(SKU, data["sku_id"])
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    order_line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == data["order_id"],
            OrderLine.sku_id == data["sku_id"],
            OrderLine.booked_count < OrderLine.quantity,
        )
        .first()
    )
    if not order_line:
        raise HTTPException(
            400,
            f"SKU {sku.sku_code} is al volledig geboekt in deze order",
        )

    booking = Booking(
        order_id=data["order_id"],
        order_line_id=order_line.id,
        sku_id=data["sku_id"],
        scanned_by=user.id,
        scan_image_path=data.get("scan_image_path"),
        confidence=data.get("confidence"),
    )
    db.add(booking)
    order_line.booked_count += 1
    db.flush()

    all_booked = all(l.booked_count >= l.quantity for l in order.lines)
    if all_booked:
        order.status = "completed"

    db.commit()

    rolcontainer = f"KLANT {order_line.customer_name.upper()}"

    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "confidence": data.get("confidence"),
            "rolcontainer": rolcontainer,
            "klant": order_line.customer_name,
            "order_completed": all_booked,
            "confirmed_by_human": True,
        },
        user=user,
        resource_type="booking",
        resource_id=booking.id,
    )

    return BookingResponse(
        id=booking.id,
        order_id=order.id,
        order_reference=order.reference,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        created_at=booking.created_at,
    )


@router.post("/new-product", response_model=SKUResponse)
@observe()
async def create_product_inline(
    file: UploadFile,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
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

    # Duplicate image detection via perceptual hash
    img_hash = compute_image_hash(image_bytes)
    duplicate = (
        db.query(ReferenceImage)
        .join(SKU)
        .filter(ReferenceImage.image_hash == img_hash)
        .first()
    )
    if duplicate:
        dup_sku = db.get(SKU, duplicate.sku_id)
        dup_label = dup_sku.sku_code if dup_sku else f"SKU #{duplicate.sku_id}"
        raise HTTPException(
            409,
            f"Deze foto is al gekoppeld aan {dup_label}",
        )

    # Save reference image
    ref_dir = os.path.join(settings.upload_dir, "reference_images", str(sku.id))
    os.makedirs(ref_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    image_path = os.path.join(ref_dir, filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # Process with Vision API
    logger.info("Processing reference image for new SKU %s", sku_code)
    try:
        vision_description, embedding, is_package = await asyncio.to_thread(process_image, image_bytes)
    except Exception:
        logger.exception("Failed to process image for new SKU %s", sku_code)
        raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")

    if not is_package:
        # Roll back the SKU creation
        db.rollback()
        raise HTTPException(400, "Dit is geen doos of verpakking — upload alleen foto's van dozen")

    from app.services.embedding import assess_description_quality
    quality = assess_description_quality(vision_description)

    ref_image = ReferenceImage(
        sku_id=sku.id,
        image_path=image_path,
        image_hash=img_hash,
        vision_description=vision_description,
        embedding=embedding,
        description_quality=quality,
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
