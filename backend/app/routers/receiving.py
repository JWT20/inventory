import logging
import time
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import require_warehouse
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, Booking, InventoryBalance, Order, OrderLine, ReferenceImage, User
from app.routers.inventory import apply_stock_movement
from app.routers.skus import _check_duplicate_embedding, _sku_to_response
from app.schemas import AlternativeMatch, BookingConfirmation, BookingResponse, ConfirmBookingRequest, MatchResult, SKUResponse
from app.services.storage import storage
from langfuse import observe, propagate_attributes

from app.services.embedding import assess_description_quality, process_image
from app.services.matching import find_best_matches

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
CONFIRMATION_TOKEN_MAX_AGE = 120  # seconds

_signer = URLSafeTimedSerializer(settings.secret_key, salt="booking-confirm")


def _image_url(key: str | None) -> str:
    """Return a browser-accessible URL for a storage key."""
    if key:
        return storage.url(key)
    return ""


def _all_reference_image_urls(db: Session, sku_id: int) -> list[str]:
    """Return URLs for all reference images of a SKU."""
    images = (
        db.query(ReferenceImage)
        .filter(ReferenceImage.sku_id == sku_id, ReferenceImage.image_path.isnot(None))
        .order_by(ReferenceImage.created_at)
        .all()
    )
    return [_image_url(img.image_path) for img in images if img.image_path]


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
        scan_key = f"scans/{uuid.uuid4().hex}.jpg"
        storage.save(scan_key, image_bytes)
        t_save = time.perf_counter()

        try:
            description, embedding, is_package = await process_image(image_bytes)
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
                            reference_image_url=_image_url(img_path),
                            reference_image_urls=_all_reference_image_urls(db, s.id),
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
            scan_image_url=_image_url(scan_key),
            reference_image_urls=_all_reference_image_urls(db, matched_sku.id),
        )


@router.post("/book", response_model=BookingConfirmation)
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
        scan_key = f"scans/{uuid.uuid4().hex}.jpg"
        storage.save(scan_key, image_bytes)
        t_save = time.perf_counter()

        # Vision: classify + describe + embed
        try:
            description, embedding, is_package = await process_image(image_bytes)
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

        # Collect quality/confidence/ambiguity reasons for logging.
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
                            reference_image_url=_image_url(img_path),
                            reference_image_urls=_all_reference_image_urls(db, s.id),
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
                        reference_image_url=_image_url(img_path),
                        reference_image_urls=_all_reference_image_urls(db, s.id),
                    ))

        if reason:
            logger.info(
                "SKU %s flagged for confirmation: %s",
                matched_sku.sku_code, ", ".join(reason),
            )

        token_data = {
            "order_id": order_id,
            "sku_id": matched_sku.id,
            "confidence": round(confidence, 4),
            "scan_image_key": scan_key,
            "user_id": user.id,
        }
        token = _signer.dumps(token_data)

        # Generate a confirmation token for each alternative
        for alt in alternatives:
            alt_token_data = {
                "order_id": order_id,
                "sku_id": alt.sku_id,
                "confidence": round(alt.confidence, 4),
                "scan_image_key": scan_key,
                "user_id": user.id,
            }
            alt.confirmation_token = _signer.dumps(alt_token_data)

        # Calculate remaining quantity for the matched SKU in this order
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

        # Pre-check stock availability (with row lock to prevent race conditions)
        balance = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == matched_sku.id,
                InventoryBalance.organization_id == order.organization_id,
            )
            .with_for_update()
            .first()
        )
        if not balance or balance.quantity_on_hand < 1:
            raise HTTPException(
                409,
                f"Geen voorraad voor {matched_sku.sku_code} — is de pakbon al ingeboekt?",
            )

        remaining = order_line.quantity - order_line.booked_count

        t_done = time.perf_counter()
        logger.info(
            "[TIMING] book total=%.0fms | read=%.0fms save=%.0fms process_image=%.0fms matching=%.0fms",
            (t_done - t_start) * 1000,
            (t_read - t_start) * 1000,
            (t_save - t_read) * 1000,
            (t_process - t_save) * 1000,
            (t_match - t_process) * 1000,
        )

        rolcontainer = f"KLANT {order_line.customer_name.upper()}"

        return BookingConfirmation(
            confirmation_token=token,
            sku_code=matched_sku.sku_code,
            sku_name=matched_sku.name,
            confidence=confidence,
            klant=order_line.customer_name,
            rolcontainer=rolcontainer,
            scan_image_url=_image_url(scan_key),
            reference_image_url=_image_url(matched_image_path),
            reference_image_urls=_all_reference_image_urls(db, matched_sku.id),
            alternatives=alternatives,
            remaining_quantity=remaining,
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

    available = order_line.quantity - order_line.booked_count
    quantity = min(body.quantity, available)

    last_booking = None
    for _ in range(quantity):
        booking = Booking(
            order_id=data["order_id"],
            order_line_id=order_line.id,
            sku_id=data["sku_id"],
            scanned_by=user.id,
            scan_image_path=data.get("scan_image_key", data.get("scan_image_path")),
            confidence=data.get("confidence"),
        )
        db.add(booking)
        last_booking = booking
    order_line.booked_count += quantity
    db.flush()

    # Deduct stock
    apply_stock_movement(
        db,
        sku_id=data["sku_id"],
        organization_id=order.organization_id,
        quantity=-quantity,
        movement_type="pick",
        reference_type="booking",
        reference_id=last_booking.id,
        performed_by=user.id,
    )

    all_booked = all(l.booked_count >= l.quantity for l in order.lines)
    if all_booked:
        order.status = "completed"

    db.commit()

    rolcontainer = f"KLANT {order_line.customer_name.upper()}"
    remaining = order_line.quantity - order_line.booked_count

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
            "quantity": quantity,
        },
        user=user,
        resource_type="booking",
        resource_id=last_booking.id,
    )

    scan_key = data.get("scan_image_key", data.get("scan_image_path", ""))
    return BookingResponse(
        id=last_booking.id,
        order_id=order.id,
        order_reference=order.reference,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        created_at=last_booking.created_at,
        scan_image_url=_image_url(scan_key) if scan_key else "",
        reference_image_urls=_all_reference_image_urls(db, sku.id),
        confidence=data.get("confidence", 0.0),
        booked_quantity=quantity,
        remaining_quantity=remaining,
    )


@router.post("/book/more", response_model=BookingResponse)
@observe()
def book_more(
    order_id: int = Form(...),
    sku_id: int = Form(...),
    quantity: int = Form(..., ge=1),
    scan_image_path: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Book additional identical boxes without re-scanning.

    Used after an initial scan+book to add more of the same SKU.
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")

    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    order_line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == order_id,
            OrderLine.sku_id == sku_id,
            OrderLine.booked_count < OrderLine.quantity,
        )
        .first()
    )
    if not order_line:
        raise HTTPException(
            400,
            f"SKU {sku.sku_code} is al volledig geboekt in deze order",
        )

    available = order_line.quantity - order_line.booked_count
    actual_quantity = min(quantity, available)

    last_booking = None
    for _ in range(actual_quantity):
        booking = Booking(
            order_id=order_id,
            order_line_id=order_line.id,
            sku_id=sku_id,
            scanned_by=user.id,
            scan_image_path=scan_image_path or None,
            confidence=None,
        )
        db.add(booking)
        last_booking = booking
    order_line.booked_count += actual_quantity
    db.flush()

    # Deduct stock
    apply_stock_movement(
        db,
        sku_id=sku_id,
        organization_id=order.organization_id,
        quantity=-actual_quantity,
        movement_type="pick",
        reference_type="booking",
        reference_id=last_booking.id,
        performed_by=user.id,
    )

    all_booked = all(l.booked_count >= l.quantity for l in order.lines)
    if all_booked:
        order.status = "completed"

    db.commit()

    rolcontainer = f"KLANT {order_line.customer_name.upper()}"
    remaining = order_line.quantity - order_line.booked_count

    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "rolcontainer": rolcontainer,
            "klant": order_line.customer_name,
            "order_completed": all_booked,
            "quantity": actual_quantity,
            "batch_add": True,
        },
        user=user,
        resource_type="booking",
        resource_id=last_booking.id,
    )

    return BookingResponse(
        id=last_booking.id,
        order_id=order.id,
        order_reference=order.reference,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        created_at=last_booking.created_at,
        scan_image_url=_image_url(scan_image_path) if scan_image_path else "",
        reference_image_urls=_all_reference_image_urls(db, sku.id),
        booked_quantity=actual_quantity,
        remaining_quantity=remaining,
    )


@router.post("/new-product", response_model=SKUResponse)
@observe()
async def create_product_inline(
    file: UploadFile,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Quick-create a new SKU with a reference image from the camera.

    Used when a scanned box is not recognized.
    """
    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' already exists")

    sku = SKU(sku_code=sku_code, name=name, description=description, category=category)
    db.add(sku)
    db.flush()

    image_bytes = _read_image(file)

    # Process with Vision API
    logger.info("Processing reference image for new SKU %s", sku_code)
    try:
        vision_description, embedding, is_package = await process_image(image_bytes)
    except Exception:
        logger.exception("Failed to process image for new SKU %s", sku_code)
        raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")

    if not is_package:
        db.rollback()
        raise HTTPException(400, "Dit is geen doos of verpakking — upload alleen foto's van dozen")

    # Duplicate detection via embedding similarity (check against all existing SKUs)
    dup_sku, similarity = _check_duplicate_embedding(db, embedding, exclude_sku_id=sku.id)
    if dup_sku:
        db.rollback()
        raise HTTPException(
            409,
            f"Deze foto lijkt te veel op een foto van {dup_sku.sku_code} (gelijkenis: {similarity:.0%})",
        )

    # Save reference image
    image_key = f"reference_images/{sku.id}/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)

    from app.services.embedding import assess_description_quality
    quality = assess_description_quality(vision_description)

    ref_image = ReferenceImage(
        sku_id=sku.id,
        image_path=image_key,
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


@router.post("/concept-product", response_model=SKUResponse, status_code=201)
def create_concept_product(
    supplier_code: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Create an inactive concept product to be completed by merchant/admin."""
    code = supplier_code.strip().upper()
    if not code:
        raise HTTPException(400, "supplier_code is verplicht")

    base_query = db.query(SKU).filter(SKU.sku_code == code)
    if user.organization_id is not None:
        existing = base_query.filter(SKU.organization_id == user.organization_id).first()
    else:
        existing = base_query.filter(SKU.organization_id.is_(None)).first()

    if existing:
        return _sku_to_response(existing)

    # A SKU with this code exists but is not visible to the current user
    other_org_sku = base_query.first()
    if other_org_sku:
        raise HTTPException(status_code=409, detail="SKU with this code exists in another organization")
    concept_name = (description or "").strip() or f"Concept {code}"
    sku = SKU(
        sku_code=code,
        name=concept_name,
        description=concept_name,
        category="other",
        active=False,
        organization_id=user.organization_id,
    )
    sku.set_attributes({"status": "concept", "source": "inbound_scan"})
    db.add(sku)
    db.commit()
    db.refresh(sku)

    publish_event(
        "concept_product_created",
        details={"sku_code": sku.sku_code, "name": sku.name, "source": "inbound_scan"},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )

    return _sku_to_response(sku)
