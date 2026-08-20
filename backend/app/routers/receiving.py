import logging
import time
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.auth import assert_order_module, require_inbound_booker
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import SKU, InventoryBalance, Order, OrderLine, Organization, ReferenceImage, User
from app.routers.skus import _check_duplicate_embedding, _sku_to_response
from app.services.booking import (
    apply_booking,
    promote_pending_images_orders_for_sku,
    rolcontainer_label,
)
from app.schemas import (
    AlternativeMatch,
    BookingConfirmation,
    BookingResponse,
    ConfirmBookingRequest,
    MatchResult,
    MissingReferenceCandidate,
    RegisterReferenceRequest,
    SKUDistributionLine,
    SKUDistributionResponse,
    SKUResponse,
)
from app.services.storage import storage
from langfuse import observe, propagate_attributes

from app.services.allocation import compute_allocation
from app.services.embedding import (
    assess_description_quality,
    generate_embedding,
    process_image,
)
from app.services.matching import find_best_matches
from app.services.product_status import recompute_active
from app.services.rerank import (
    RerankVerdict,
    needs_visual_check,
    rerank_scan,
    select_rerank_candidates,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
# Vector hits pulled before the visual rerank narrows them down. Deep enough
# that a whole cluster of near-identical variants fits in the result set —
# with lookalikes the true SKU may sit a few ranks below the wrong one.
CATALOG_SEARCH_TOP_N = 10
CONFIRMATION_TOKEN_MAX_AGE = 120  # seconds

_signer = URLSafeTimedSerializer(settings.secret_key, salt="booking-confirm")
_register_signer = URLSafeTimedSerializer(settings.secret_key, salt="register-reference")
REGISTER_TOKEN_MAX_AGE = 300  # seconds


# Image processing states that count as "the SKU has a usable reference image".
_USABLE_REF_STATUSES = ("pending", "processing", "done")
_DELIVERY_DAY_SORT = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
}


def _scope_label(context_order: "Order") -> str:
    return "de open orders" if context_order.delivery_week else "deze order"


def _open_scope_lines_query(
    db: Session,
    context_order: "Order",
    sku_id: int | None = None,
    include_complete: bool = False,
):
    """Open active order lines across all open weeks, or the order itself as fallback.

    A scheduled order (with a delivery_week) opens up matching against every active
    scheduled order line in the organization, regardless of week — the koerier can keep
    scanning incoming boxes and each one is routed to whichever open order line matches.
    An ad-hoc order (no delivery_week) stays scoped to itself and is never swept into
    weekly matching.

    With ``include_complete=True`` fully-booked lines are kept too, so a read-only
    overview can show already-finished customers with a ✓ instead of hiding them.
    """
    query = (
        db.query(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .filter(Order.status == "active")
    )
    if not include_complete:
        query = query.filter(OrderLine.booked_count < OrderLine.quantity)
    if context_order.delivery_week:
        query = query.filter(
            Order.organization_id == context_order.organization_id,
            Order.delivery_week.isnot(None),
        )
    else:
        query = query.filter(Order.id == context_order.id)
    if sku_id is not None:
        query = query.filter(OrderLine.sku_id == sku_id)
    return query


def _scope_sku_ids(db: Session, context_order: "Order") -> list[int]:
    lines = _open_scope_lines_query(db, context_order).all()
    return sorted({line.sku_id for line in lines})


def _cap_remaining_by_line(
    db: Session,
    context_order: "Order",
    sku_id: int,
    lines: list["OrderLine"],
) -> dict[int, int]:
    """Return remaining bookable quantity per line, respecting caps per (week, day).

    Caps are computed independently per delivery week so widening the scan scope
    across weeks never lets one week's allocation eat into another's.
    """
    if not context_order.delivery_week:
        return {
            line.id: max(0, line.quantity - line.booked_count)
            for line in lines
        }

    caps_by_line: dict[int, int] = {}
    groups = sorted(
        {(line.order.delivery_week, line.delivery_day) for line in lines},
        key=lambda g: (g[0] or "", _DELIVERY_DAY_SORT.get(g[1], 9)),
    )
    for week, delivery_day in groups:
        group_lines = [
            line for line in lines
            if line.order.delivery_week == week and line.delivery_day == delivery_day
        ]
        caps = compute_allocation(
            db,
            week,
            sku_id,
            context_order.organization_id,
            delivery_day,
        )
        for line in group_lines:
            cap_total = caps.get(line.id, line.booked_count)
            caps_by_line[line.id] = max(0, cap_total - line.booked_count)
    return caps_by_line


def _select_order_line_for_scope(
    db: Session,
    context_order: "Order",
    sku_id: int,
) -> tuple["OrderLine", int]:
    """Pick the exact order line for a scan: start order first, then week fallback."""
    lines = _open_scope_lines_query(db, context_order, sku_id).all()
    if not lines:
        raise HTTPException(
            400,
            f"SKU staat niet open in {_scope_label(context_order)}",
        )

    cap_remaining_by_line = _cap_remaining_by_line(db, context_order, sku_id, lines)
    candidates = [
        (line, min(line.quantity - line.booked_count, cap_remaining_by_line.get(line.id, 0)))
        for line in lines
        if cap_remaining_by_line.get(line.id, 0) > 0
    ]
    if not candidates:
        raise HTTPException(
            409,
            f"Toewijzingslimiet bereikt voor deze SKU in {_scope_label(context_order)}",
        )

    def sort_key(item: tuple["OrderLine", int]) -> tuple[int, str, int, int]:
        line, _cap_remaining = item
        return (
            0 if line.order_id == context_order.id else 1,
            line.order.delivery_week or "",
            _DELIVERY_DAY_SORT.get(line.delivery_day, 9),
            line.id,
        )

    return sorted(candidates, key=sort_key)[0]


def _missing_reference_candidates(
    db: Session,
    context_order: "Order",
    is_bottle: bool | None = None,
) -> list[MissingReferenceCandidate]:
    """SKUs in the scan scope that have no usable reference image and still need bookings.

    When *is_bottle* is set, only SKUs of that unit type are offered — a bottle
    scan should never become the reference image of a box product.
    """
    by_sku: dict[int, MissingReferenceCandidate] = {}
    for line in _open_scope_lines_query(db, context_order).all():
        if line.booked_count >= line.quantity:
            continue
        sku = line.sku
        if is_bottle is not None and sku.is_bottle != is_bottle:
            continue
        if not any(img.processing_status in _USABLE_REF_STATUSES for img in sku.reference_images):
            remaining = line.quantity - line.booked_count
            existing = by_sku.get(sku.id)
            if existing:
                existing.remaining_quantity += remaining
            else:
                by_sku[sku.id] = MissingReferenceCandidate(
                    sku_id=sku.id,
                    sku_code=sku.sku_code,
                    sku_name=sku.name,
                    remaining_quantity=remaining,
                )
    return list(by_sku.values())


def _confirmation_token_data(
    context_order: "Order",
    order_line: "OrderLine",
    sku_id: int,
    confidence: float,
    scan_key: str,
    user_id: int,
) -> dict:
    return {
        "context_order_id": context_order.id,
        "order_id": order_line.order_id,
        "order_line_id": order_line.id,
        "sku_id": sku_id,
        "confidence": round(confidence, 4),
        "scan_image_key": scan_key,
        "user_id": user_id,
    }


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
    prefix="/receiving", tags=["receiving"], dependencies=[Depends(require_inbound_booker)]
)


@router.post("/identify", response_model=MatchResult | None)
@observe()
async def identify_box(
    file: UploadFile,
    scan_mode: str = Form("box"),
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Scan a box or bottle and identify it against reference images.

    ``scan_mode`` selects the match pool: box scans only match box
    references, bottle scans only bottle references. In bottle mode the
    is_package gate is skipped — the courier chose the mode deliberately.
    Returns the matched SKU, or null if no match found.
    """
    bottle_mode = scan_mode == "bottle"
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

        if not is_package and not bottle_mode:
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
                    "scan_mode": scan_mode,
                },
                user=user,
                resource_type="receiving",
            )
            return None

        # In bottle mode a scan the classifier rejected still proceeds; the
        # description was not embedded yet (process_image skips that), so do
        # it here before matching.
        if embedding is None:
            embedding = await generate_embedding(description)

        matches = find_best_matches(
            db, embedding, top_n=CATALOG_SEARCH_TOP_N, is_bottle=bottle_mode
        )
        t_match = time.perf_counter()

        # Same visual second pass as booking, on the same close-call trigger,
        # so herkennen and boeken cannot disagree about a photo. There is no
        # order here, so only the ambiguity trigger applies.
        needs_check, check_reason = needs_visual_check(matches)
        if needs_check:
            logger.info("Visual check triggered: %s", check_reason)
            verdict = await rerank_scan(
                image_bytes, select_rerank_candidates(db, matches)
            )
        else:
            verdict = RerankVerdict(ran=False, skip_reason=check_reason)
        by_sku_id = {m[0].id: m for m in matches}

        matched_sku, confidence, matched_ref_desc = None, 0.0, None
        if verdict.is_confident and verdict.sku_id in by_sku_id:
            matched_sku, confidence, _path, matched_ref_desc = by_sku_id[verdict.sku_id]
        elif (
            not verdict.rejected_all
            and matches
            and matches[0][1] >= settings.match_threshold
        ):
            matched_sku, confidence = matches[0][0], matches[0][1]
            matched_ref_desc = matches[0][3]

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
            for s, sim, _img_path, ref_desc in matches
        ]

        publish_event(
            "box_identified",
            details={
                "matched_sku_code": matched_sku.sku_code if matched_sku else None,
                "confidence": round(confidence, 4) if matched_sku else None,
                "vision_description": description,
                "candidates": candidate_details,
                "threshold": settings.match_threshold,
                "scan_mode": scan_mode,
                "rerank_ran": verdict.ran,
                "rerank_sku_id": verdict.sku_id,
                "rerank_certainty": verdict.certainty if verdict.ran else None,
                "rerank_feature": verdict.distinguishing_feature or None,
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
        if verdict.degraded:
            reasons.append(f"visual check unavailable ({verdict.skip_reason})")
        elif verdict.ran and verdict.certainty != "high":
            reasons.append("visual check was not certain")
        if verdict.is_confident and verdict.distinguishing_feature:
            reasons.append(f"visual check: {verdict.distinguishing_feature}")

        # Every rival close enough to be confusable is offered, measured from
        # the match that was actually chosen — after a rerank that need not be
        # the top vector hit.
        for s, sim, img_path, _ref_desc in matches:
            if s.id == matched_sku.id:
                continue
            if confidence - sim >= settings.ambiguity_margin:
                continue
            reasons.append(
                f"ambiguous match ({s.sku_code} at {sim:.3f} vs best match at {confidence:.3f})"
            )
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


@router.get("/distribution", response_model=SKUDistributionResponse)
def sku_distribution(
    order_id: int,
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Read-only verdeel-lijst for a scanned SKU.

    After a scan identifies a SKU, this shows which customers the SKU still needs
    to go to across the open scope (the whole week for scheduled orders, or just
    the order itself for ad-hoc ones), with the fair allocation cap as ``remaining``.
    Pure display: it books nothing and never touches stock.
    """
    context_order = db.get(Order, order_id)
    if not context_order:
        raise HTTPException(404, "Order niet gevonden")
    # Owners/members may only read their own organization's orders. Platform admins
    # and couriers serve across organizations, so they are unrestricted — same guard
    # the concept-product endpoint applies.
    if (
        user.role in ("owner", "member")
        and context_order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    assert_order_module(context_order, "vision_picking", user)
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")
    # The verdeel-lijst is always scoped to the context order's organization (caps and
    # stock below use it), so a SKU from another org has no place here. Reject as "not
    # found" rather than leaking its code/name across the tenant boundary. Global SKUs
    # (organization_id is None) are shared and stay visible.
    if sku.organization_id not in (None, context_order.organization_id):
        raise HTTPException(404, "SKU niet gevonden")

    # Scope to the context order's own delivery week: the koerier is distributing
    # this week's boxes, not every future week's open orders. Eager-load customer and
    # order to avoid an N+1 over the result set.
    query = _open_scope_lines_query(db, context_order, sku_id, include_complete=True)
    if context_order.delivery_week:
        query = query.filter(Order.delivery_week == context_order.delivery_week)
    lines = query.options(
        contains_eager(OrderLine.order),
        joinedload(OrderLine.customer),
    ).all()
    cap_remaining = _cap_remaining_by_line(db, context_order, sku_id, lines)

    dist_lines = [
        SKUDistributionLine(
            order_id=line.order_id,
            order_line_id=line.id,
            customer_name=line.customer_name,
            rolcontainer=rolcontainer_label(line),
            delivery_day=line.delivery_day,
            delivery_week=line.order.delivery_week,
            ordered_quantity=line.quantity,
            booked_count=line.booked_count,
            remaining_quantity=cap_remaining.get(line.id, max(0, line.quantity - line.booked_count)),
            is_complete=line.booked_count >= line.quantity,
            is_context_order=line.order_id == context_order.id,
        )
        for line in lines
    ]
    # Context order first, then week, delivery day, customer — stable and scannable.
    dist_lines.sort(
        key=lambda d: (
            0 if d.is_context_order else 1,
            d.delivery_week or "",
            _DELIVERY_DAY_SORT.get(d.delivery_day, 9),
            d.customer_name.lower(),
        )
    )

    # Per-(week, day) caps are each computed against the same shared stock, so the rows
    # can collectively promise more boxes than physically exist. Hand out the real
    # available stock across the rows in display order (context order first) so no row —
    # and therefore the headline total — ever overpromises what is on hand.
    if context_order.delivery_week:
        balance = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == sku_id,
                InventoryBalance.organization_id == context_order.organization_id,
                InventoryBalance.inventory_location == context_order.inventory_location,
            )
            .first()
        )
        budget = balance.quantity_available if balance else 0
        for d in dist_lines:
            d.remaining_quantity = min(d.remaining_quantity, budget)
            budget -= d.remaining_quantity
    total_remaining = sum(d.remaining_quantity for d in dist_lines)

    return SKUDistributionResponse(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        scope=_scope_label(context_order),
        total_remaining=total_remaining,
        lines=dist_lines,
    )


@router.post("/book", response_model=BookingConfirmation)
@observe()
async def book_box(
    file: UploadFile,
    order_id: int = Form(...),
    scan_mode: str = Form("box"),
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """1 scan = 1 besteleenheid (doos of fles) = 1 booking.

    Scans the box or bottle, identifies the SKU, finds the matching order
    line, and creates a booking. Returns the rolcontainer assignment.
    ``scan_mode`` restricts matching to box or bottle references; in bottle
    mode the is_package gate is skipped.
    """
    bottle_mode = scan_mode == "bottle"
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
        # Photo/AI picking only runs for orders whose organization has the
        # vision-picking module. Keyed on the order's org, not the user's:
        # couriers have no org and serve across merchants.
        assert_order_module(order, "vision_picking", user)

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

        if not is_package and not bottle_mode:
            publish_event(
                "box_booked",
                details={
                    "order_reference": order.reference,
                    "rejected": True,
                    "rejection_reason": "not_a_package",
                    "vision_description": description,
                    "scan_mode": scan_mode,
                },
                user=user,
                resource_type="booking",
            )
            raise HTTPException(
                422,
                "Dit is geen doos of verpakking — scan een productdoos",
            )

        unit_word = "fles" if bottle_mode else "doos"

        # Bottle mode skips the gate, but a rejected classification means the
        # description was never embedded — do that now so matching can run.
        if embedding is None:
            embedding = await generate_embedding(description)

        # Match across all open orders; the context order is booked first and
        # other weeks remain available as a FIFO fallback.
        scope_sku_ids = set(_scope_sku_ids(db, order))
        if not scope_sku_ids:
            raise HTTPException(
                400,
                "Geen open orderregels gevonden",
            )

        # Search the whole catalogue, never only the SKUs open in scope.
        # Restricting the pool to the order's own SKUs is what let a lookalike
        # from another product line pass as a match: with three near-identical
        # variants received and only one of them ordered, the scan of a wrong
        # variant still cleared the threshold because the variant it actually
        # was had been excluded from the pool and could not outscore it.
        catalogue_matches = find_best_matches(
            db, embedding, top_n=CATALOG_SEARCH_TOP_N, is_bottle=bottle_mode
        )
        # The catalogue LIMIT must never make an open order SKU disappear. A
        # large lookalike cluster can occupy every global top-N slot even though
        # the correct SKU is the next result. Pull the strongest open candidates
        # separately and merge them back before the visual pass.
        scope_matches = find_best_matches(
            db,
            embedding,
            top_n=max(1, settings.rerank_max_candidates),
            sku_ids=list(scope_sku_ids),
            is_bottle=bottle_mode,
        )
        matches_by_sku_id = {match[0].id: match for match in catalogue_matches}
        for match in scope_matches:
            current = matches_by_sku_id.get(match[0].id)
            if current is None or match[1] > current[1]:
                matches_by_sku_id[match[0].id] = match
        matches = sorted(
            matches_by_sku_id.values(), key=lambda match: match[1], reverse=True
        )
        t_match = time.perf_counter()

        # Second pass, on the photos themselves — but only for a close call.
        # Cosine similarity compares paraphrases of the packaging; two variants
        # that differ in one printed word paraphrase almost identically, so the
        # ordering between them is noise. Showing the model the scan next to the
        # candidates' reference photos is the comparison that can separate them.
        # A match that leads its runner-up by a wide margin needs no such help.
        needs_check, check_reason = needs_visual_check(matches, scope_sku_ids)
        if needs_check:
            logger.info("Visual check triggered: %s", check_reason)
            verdict = await rerank_scan(
                image_bytes,
                select_rerank_candidates(db, matches, scope_sku_ids=scope_sku_ids),
            )
        else:
            verdict = RerankVerdict(ran=False, skip_reason=check_reason)

        by_sku_id = {m[0].id: m for m in matches}
        vector_best = matches[0] if matches else None
        in_scope_matches = [m for m in matches if m[0].id in scope_sku_ids]

        chosen: tuple[SKU, float, str | None, str | None] | None = None
        manual_review_required = False
        if verdict.is_confident and verdict.sku_id in by_sku_id:
            chosen = by_sku_id[verdict.sku_id]
        elif not verdict.rejected_all and vector_best and vector_best[1] >= settings.match_threshold:
            chosen = vector_best
        elif verdict.rejected_all and vector_best:
            # The visual pass can reject every photo even though the strongest
            # open-order candidate is still a plausible, near-tied vector hit
            # (for example when its reference photo is stale or poor). Do not
            # dead-end the picker in that narrow case: offer exactly that one
            # order candidate for explicit manual approval.
            #
            # The guardrails deliberately mirror the normal match and
            # ambiguity thresholds. A materially better catalogue hit must not
            # be forced onto the order, and a SKU the visual pass never saw is
            # not eligible for this fallback.
            best_in_scope = next(iter(in_scope_matches), None)
            if (
                best_in_scope is not None
                and best_in_scope[1] >= settings.match_threshold
                and vector_best[1] - best_in_scope[1] <= settings.ambiguity_margin
                and best_in_scope[0].id in verdict.considered_sku_ids
                and best_in_scope[2] is not None
            ):
                chosen = best_in_scope
                manual_review_required = True

        # A confident verdict for a SKU that is not open here means the picker
        # is holding the wrong box. Never offer a bookable proposal in that
        # case — a one-tap confirm is exactly how the wrong variant got booked.
        if chosen is not None and chosen[0].id not in scope_sku_ids and verdict.is_confident:
            wrong_sku = chosen[0]
            detail = (
                f"Deze {unit_word} is {wrong_sku.sku_code} ({wrong_sku.name}), "
                f"maar die staat niet open in {_scope_label(order)}"
            )
            if verdict.distinguishing_feature:
                detail += f" — {verdict.distinguishing_feature}"
            raise HTTPException(409, detail)

        # Unsure, and the best guess is out of scope: fall back to the best
        # candidate that *is* open, but only as a proposal to confirm, with the
        # out-of-scope lookalike shown alongside it (handled below).
        out_of_scope_lookalike: tuple[SKU, float, str | None, str | None] | None = None
        if chosen is not None and chosen[0].id not in scope_sku_ids:
            out_of_scope_lookalike = chosen
            chosen = next(
                (m for m in in_scope_matches if m[1] >= settings.match_threshold), None
            )

        matched_sku, confidence, matched_image_path, matched_ref_desc = None, 0.0, None, None
        if chosen is not None:
            matched_sku, confidence, matched_image_path, matched_ref_desc = chosen

        if matched_sku is None:
            # The scan matched a SKU outside this week's open order lines.
            fallback_lookalike = out_of_scope_lookalike or (
                vector_best
                if vector_best and vector_best[1] >= settings.match_threshold
                else None
            )
            if fallback_lookalike is not None and not verdict.rejected_all:
                wrong_sku = fallback_lookalike[0]
                raise HTTPException(
                    409,
                    f"Deze {unit_word} lijkt op SKU {wrong_sku.sku_code} ({wrong_sku.name}), "
                    f"maar die staat niet open in {_scope_label(order)}",
                )

            # Surface SKUs in this scan scope that have no reference image yet —
            # matching cannot succeed against them. The koerier is holding the
            # bottle right now, so they can pick which SKU this box is for and
            # the scan becomes the first reference image.
            missing_refs = _missing_reference_candidates(db, order, is_bottle=bottle_mode)
            if missing_refs:
                register_token = _register_signer.dumps({
                    "context_order_id": order_id,
                    "scan_image_key": scan_key,
                    "user_id": user.id,
                })
                raise HTTPException(
                    422,
                    detail={
                        "error": "needs_reference_image",
                        "message": (
                            f"{unit_word.capitalize()} niet herkend. Eén of meer SKUs in {_scope_label(order)} "
                            "hebben nog geen referentiefoto. Kies de juiste SKU "
                            "om deze scan als referentiefoto te registreren."
                        ),
                        "register_token": register_token,
                        "scan_image_url": _image_url(scan_key),
                        "candidates": [c.model_dump() for c in missing_refs],
                    },
                )

            raise HTTPException(
                404,
                f"{unit_word.capitalize()} niet herkend — geen match gevonden met open SKUs in {_scope_label(order)}",
            )

        # Collect quality/confidence/ambiguity reasons for logging.
        CONFIRM_THRESHOLD = 0.84
        quality = assess_description_quality(description)
        reason: list[str] = []
        alternatives: list[AlternativeMatch] = []

        if manual_review_required:
            reason.append(
                "Automatische fotovergelijking kon geen zekere match bevestigen. "
                "Vergelijk de scan met de referentiefoto en bevestig alleen als dit product klopt."
            )
        else:
            if quality == "low":
                reason.append("low-quality description")
            if confidence < CONFIRM_THRESHOLD:
                reason.append(f"low confidence ({confidence:.2f} < {CONFIRM_THRESHOLD})")
            if verdict.degraded:
                # The scan was a close call and the visual pass could not be made,
                # so the proposal rests on the embedding alone — precisely what
                # cannot separate lookalikes. The picker gets the last word rather
                # than a silent auto-proposal.
                reason.append(f"visual check unavailable ({verdict.skip_reason})")
            elif verdict.ran and verdict.certainty != "high":
                reason.append("visual check was not certain")
            if verdict.is_confident and verdict.distinguishing_feature:
                reason.append(f"visual check: {verdict.distinguishing_feature}")

        def _add_alternative(
            sku: SKU, sim: float, img_path: str | None, *, note: str = ""
        ) -> None:
            if sku.id == matched_sku.id or any(a.sku_id == sku.id for a in alternatives):
                return
            alternatives.append(AlternativeMatch(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.name,
                confidence=sim,
                reference_image_url=_image_url(img_path),
                reference_image_urls=_all_reference_image_urls(db, sku.id),
                bookable=sku.id in scope_sku_ids,
                note=note,
            ))

        # Every lookalike close enough to be confusable goes on the screen —
        # including the ones that are not open here. The out-of-scope ones
        # cannot be booked, but they are usually the box actually in the
        # picker's hands, and seeing that photo is what stops the mis-pick.
        # The rejected-all fallback intentionally shows one option only. Its
        # purpose is a simple scan-vs-reference decision, not another candidate
        # picker. All existing uncertain/degraded paths keep their alternatives.
        if not manual_review_required:
            if out_of_scope_lookalike is not None:
                s, sim, img_path, _ref_desc = out_of_scope_lookalike
                reason.append(f"closest catalogue match {s.sku_code} is not open in scope")
                _add_alternative(
                    s, sim, img_path,
                    note=f"staat niet open in {_scope_label(order)}",
                )

            for s, sim, img_path, _ref_desc in matches:
                if s.id == matched_sku.id:
                    continue
                if sim < confidence - settings.ambiguity_margin:
                    continue
                if s.id in scope_sku_ids:
                    reason.append(
                        f"close match in scope ({s.sku_code} at {sim:.3f} vs best match at {confidence:.3f})"
                    )
                    _add_alternative(s, sim, img_path)
                else:
                    reason.append(
                        f"close catalogue match out of scope ({s.sku_code} at {sim:.3f})"
                    )
                    _add_alternative(
                        s, sim, img_path,
                        note=f"staat niet open in {_scope_label(order)}",
                    )

        if reason:
            logger.info(
                "SKU %s flagged for confirmation: %s",
                matched_sku.sku_code, ", ".join(reason),
            )

        order_line, cap_remaining_for_line = _select_order_line_for_scope(db, order, matched_sku.id)
        booking_order = order_line.order
        token = _signer.dumps(_confirmation_token_data(
            order, order_line, matched_sku.id, confidence, scan_key, user.id
        ))

        # Generate a confirmation token for each bookable alternative. An
        # alternative that turns out to have no bookable order line is demoted
        # to a warning rather than dropped: hiding it would hide the very
        # lookalike the picker needs to rule out.
        for alt in alternatives:
            if not alt.bookable:
                continue
            try:
                alt_line, _alt_cap_remaining = _select_order_line_for_scope(db, order, alt.sku_id)
            except HTTPException:
                alt.bookable = False
                alt.confirmation_token = ""
                if not alt.note:
                    alt.note = f"niet meer te boeken in {_scope_label(order)}"
                continue
            alt.confirmation_token = _signer.dumps(_confirmation_token_data(
                order, alt_line, alt.sku_id, alt.confidence, scan_key, user.id
            ))

        # Pre-check stock availability (with row lock to prevent race conditions)
        balance = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id == matched_sku.id,
                InventoryBalance.organization_id == booking_order.organization_id,
                InventoryBalance.inventory_location == booking_order.inventory_location,
            )
            .with_for_update()
            .first()
        )
        if not balance or balance.quantity_available < 1:
            raise HTTPException(
                409,
                f"Geen voorraad voor {matched_sku.sku_code} — is de pakbon al ingeboekt?",
            )

        remaining = order_line.quantity - order_line.booked_count

        # Compute allocation cap for this customer/day
        cap_for_customer: int | None = cap_remaining_for_line if order.delivery_week else None
        ordered_by_customer: int | None = order_line.quantity if order.delivery_week else None
        remaining = min(remaining, cap_remaining_for_line)

        t_done = time.perf_counter()
        logger.info(
            "[TIMING] book total=%.0fms | read=%.0fms save=%.0fms process_image=%.0fms matching=%.0fms",
            (t_done - t_start) * 1000,
            (t_read - t_start) * 1000,
            (t_save - t_read) * 1000,
            (t_process - t_save) * 1000,
            (t_match - t_process) * 1000,
        )

        rolcontainer = rolcontainer_label(order_line)

        return BookingConfirmation(
            confirmation_token=token,
            order_id=booking_order.id,
            order_line_id=order_line.id,
            order_reference=booking_order.reference,
            context_order_id=order.id,
            context_order_reference=order.reference,
            sku_code=matched_sku.sku_code,
            sku_name=matched_sku.name,
            confidence=confidence,
            klant=order_line.customer_name,
            rolcontainer=rolcontainer,
            pick_location=matched_sku.primary_location_code,
            scan_image_url=_image_url(scan_key),
            reference_image_url=_image_url(matched_image_path),
            reference_image_urls=_all_reference_image_urls(db, matched_sku.id),
            alternatives=alternatives,
            remaining_quantity=remaining,
            cap_for_customer=cap_for_customer,
            ordered_by_customer=ordered_by_customer,
            confirmation_reason=", ".join(reason) if reason else None,
            manual_review_required=manual_review_required,
        )


@router.post("/book/confirm", response_model=BookingResponse)
@observe()
def confirm_booking(
    body: ConfirmBookingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Confirm a booking that was flagged for human approval (low-quality description)."""
    try:
        data = _signer.loads(body.confirmation_token, max_age=CONFIRMATION_TOKEN_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(410, "Bevestigingstoken verlopen — scan opnieuw")
    except BadSignature:
        raise HTTPException(400, "Ongeldig bevestigingstoken")

    sku = db.get(SKU, data["sku_id"])
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    order_line = None
    if data.get("order_line_id"):
        order_line = db.get(OrderLine, data["order_line_id"])
        if order_line and order_line.sku_id != data["sku_id"]:
            raise HTTPException(400, "Bevestiging hoort niet bij deze SKU")
        if order_line and order_line.booked_count >= order_line.quantity:
            order_line = None
    else:
        # Backward-compatible path for short-lived tokens issued before this change.
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
            f"SKU {sku.sku_code} is al volledig geboekt voor deze orderregel",
        )

    order = order_line.order
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")
    assert_order_module(order, "vision_picking", user)

    available = order_line.quantity - order_line.booked_count
    quantity = min(body.quantity, available)

    # Allocation safety net: recompute cap and reject if exceeded
    if order.delivery_week and order_line.delivery_day:
        caps = compute_allocation(
            db, order.delivery_week, sku.id,
            order.organization_id, order_line.delivery_day,
        )
        cap_total = caps.get(order_line.id)
        if cap_total is not None:
            cap_remaining = max(0, cap_total - order_line.booked_count)
            if quantity > cap_remaining:
                raise HTTPException(
                    409,
                    detail={
                        "detail": "Toewijzingslimiet bereikt",
                        "error": "allocation_cap_reached",
                        "customer": order_line.customer_name,
                        "sku_name": sku.name,
                        "cap_for_this_customer": cap_total,
                        "ordered_by_this_customer": order_line.quantity,
                    },
                )
            quantity = min(quantity, cap_remaining)

    result = apply_booking(
        db,
        order_id=order.id,
        order_line_id=order_line.id,
        sku_id=data["sku_id"],
        quantity=quantity,
        cap_remaining=None,
        scanned_by=user.id,
        scan_image_path=data.get("scan_image_key", data.get("scan_image_path")),
        confidence=data.get("confidence"),
    )

    rolcontainer = rolcontainer_label(order_line)
    remaining = result.remaining

    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "is_bottle": sku.is_bottle,
            "confidence": data.get("confidence"),
            "rolcontainer": rolcontainer,
            "klant": order_line.customer_name,
            "order_completed": result.order_completed,
            "confirmed_by_human": True,
            "quantity": result.booked_quantity,
        },
        user=user,
        resource_type="booking",
        resource_id=result.last_booking_id,
    )

    scan_key = data.get("scan_image_key", data.get("scan_image_path", ""))
    context_order_reference = None
    if data.get("context_order_id"):
        context_order = db.get(Order, data["context_order_id"])
        context_order_reference = context_order.reference if context_order else None

    return BookingResponse(
        id=result.last_booking_id,
        order_id=order.id,
        order_line_id=order_line.id,
        order_reference=order.reference,
        context_order_id=data.get("context_order_id"),
        context_order_reference=context_order_reference,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        created_at=result.last_booking_created_at,
        scan_image_url=_image_url(scan_key) if scan_key else "",
        reference_image_urls=_all_reference_image_urls(db, sku.id),
        confidence=data.get("confidence", 0.0),
        booked_quantity=result.booked_quantity,
        remaining_quantity=remaining,
        order_completed=result.order_completed,
    )


@router.post("/register-reference", response_model=BookingConfirmation)
@observe()
async def register_reference_and_book(
    body: RegisterReferenceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Register the most recent scan as a reference image for the picked SKU.

    Used by the warehouse UI when /book returned 'needs_reference_image': the
    koerier holds a box that doesn't match anything because the SKU has no
    reference image yet. They pick which order line this box belongs to, and
    the scan they just took becomes the SKU's first reference image. Returns
    a BookingConfirmation token so the UI can immediately call /book/confirm
    to register the booking — one tap on the koerier's side.
    """
    try:
        data = _register_signer.loads(body.register_token, max_age=REGISTER_TOKEN_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(410, "Registratietoken verlopen — scan opnieuw")
    except BadSignature:
        raise HTTPException(400, "Ongeldig registratietoken")

    context_order_id = data.get("context_order_id", data.get("order_id"))
    scan_image_key = data["scan_image_key"]

    context_order = db.get(Order, context_order_id)
    if not context_order:
        raise HTTPException(404, "Order niet gevonden")
    if context_order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {context_order.status})")
    assert_order_module(context_order, "vision_picking", user)

    sku = db.get(SKU, body.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    order_line, cap_remaining_for_line = _select_order_line_for_scope(db, context_order, body.sku_id)
    order = order_line.order

    # Refuse to overwrite an existing reference — the koerier should only land
    # here when the SKU genuinely has no usable image.
    if any(img.processing_status in _USABLE_REF_STATUSES for img in sku.reference_images):
        raise HTTPException(
            409,
            f"SKU {sku.sku_code} heeft al een referentiefoto — scan opnieuw",
        )

    image_bytes = storage.read(scan_image_key)
    if not image_bytes:
        raise HTTPException(410, "Scanafbeelding niet meer beschikbaar — scan opnieuw")

    try:
        description, embedding, _is_package = await process_image(image_bytes)
    except Exception:
        logger.exception("Vision processing failed during reference registration")
        raise HTTPException(502, "Beeldverwerking mislukt — controleer Gemini API-configuratie")

    image_key = f"reference_images/{sku.id}/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)

    ref_image = ReferenceImage(
        sku_id=sku.id,
        image_path=image_key,
        vision_description=description,
        embedding=embedding,
        description_quality=assess_description_quality(description),
        processing_status="done",
    )
    db.add(ref_image)
    db.flush()
    recompute_active(sku, db)
    promote_pending_images_orders_for_sku(db, sku.id)
    db.commit()
    db.refresh(sku)

    publish_event(
        "reference_image_registered_at_scan",
        details={
            "sku_code": sku.sku_code,
            "order_reference": order.reference,
            "context_order_reference": context_order.reference,
        },
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )

    # Hand back a booking-confirm token so the UI can finalize the booking
    # without forcing the koerier to scan again.
    confirm_token = _signer.dumps(_confirmation_token_data(
        context_order, order_line, sku.id, 1.0, scan_image_key, user.id
    ))

    remaining = order_line.quantity - order_line.booked_count
    remaining = min(remaining, cap_remaining_for_line)
    rolcontainer = rolcontainer_label(order_line)

    return BookingConfirmation(
        confirmation_token=confirm_token,
        order_id=order.id,
        order_line_id=order_line.id,
        order_reference=order.reference,
        context_order_id=context_order.id,
        context_order_reference=context_order.reference,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        confidence=1.0,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        pick_location=sku.primary_location_code,
        scan_image_url=_image_url(scan_image_key),
        reference_image_url=_image_url(image_key),
        reference_image_urls=_all_reference_image_urls(db, sku.id),
        alternatives=[],
        remaining_quantity=remaining,
        cap_for_customer=cap_remaining_for_line if context_order.delivery_week else None,
        ordered_by_customer=order_line.quantity if context_order.delivery_week else None,
    )


@router.post("/book/more", response_model=BookingResponse)
@observe()
def book_more(
    order_line_id: int | None = Form(None),
    order_id: int | None = Form(None),
    sku_id: int | None = Form(None),
    quantity: int = Form(..., ge=1),
    scan_image_path: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Book additional identical boxes without re-scanning.

    Used after an initial scan+book to add more of the same SKU.
    """
    if order_line_id is not None:
        order_line = db.get(OrderLine, order_line_id)
        if not order_line:
            raise HTTPException(404, "Orderregel niet gevonden")
        if order_line.booked_count >= order_line.quantity:
            order_line = None
    elif order_id is not None and sku_id is not None:
        # Backward-compatible path for clients that have not yet sent order_line_id.
        order_line = (
            db.query(OrderLine)
            .filter(
                OrderLine.order_id == order_id,
                OrderLine.sku_id == sku_id,
                OrderLine.booked_count < OrderLine.quantity,
            )
            .first()
        )
    else:
        raise HTTPException(400, "order_line_id is verplicht")

    if not order_line:
        raise HTTPException(
            400,
            "Deze orderregel is al volledig geboekt",
        )

    order = order_line.order
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")
    assert_order_module(order, "vision_picking", user)

    sku = db.get(SKU, order_line.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    available = order_line.quantity - order_line.booked_count
    actual_quantity = min(quantity, available)

    # Allocation safety net: recompute cap and reject if exceeded
    if order.delivery_week and order_line.delivery_day:
        caps = compute_allocation(
            db, order.delivery_week, sku.id,
            order.organization_id, order_line.delivery_day,
        )
        cap_total = caps.get(order_line.id)
        if cap_total is not None:
            cap_remaining = max(0, cap_total - order_line.booked_count)
            if actual_quantity > cap_remaining:
                raise HTTPException(
                    409,
                    detail={
                        "detail": "Toewijzingslimiet bereikt",
                        "error": "allocation_cap_reached",
                        "customer": order_line.customer_name,
                        "sku_name": sku.name,
                        "cap_for_this_customer": cap_total,
                        "ordered_by_this_customer": order_line.quantity,
                    },
                )
            actual_quantity = min(actual_quantity, cap_remaining)

    result = apply_booking(
        db,
        order_id=order.id,
        order_line_id=order_line.id,
        sku_id=sku.id,
        quantity=actual_quantity,
        cap_remaining=None,
        scanned_by=user.id,
        scan_image_path=scan_image_path or None,
        confidence=None,
    )

    rolcontainer = rolcontainer_label(order_line)
    remaining = result.remaining

    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "is_bottle": sku.is_bottle,
            "rolcontainer": rolcontainer,
            "klant": order_line.customer_name,
            "order_completed": result.order_completed,
            "quantity": result.booked_quantity,
            "batch_add": True,
        },
        user=user,
        resource_type="booking",
        resource_id=result.last_booking_id,
    )

    return BookingResponse(
        id=result.last_booking_id,
        order_id=order.id,
        order_line_id=order_line.id,
        order_reference=order.reference,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=order_line.customer_name,
        rolcontainer=rolcontainer,
        created_at=result.last_booking_created_at,
        scan_image_url=_image_url(scan_image_path) if scan_image_path else "",
        reference_image_urls=_all_reference_image_urls(db, sku.id),
        booked_quantity=result.booked_quantity,
        remaining_quantity=remaining,
        order_completed=result.order_completed,
    )


@router.post("/new-product", response_model=SKUResponse)
@observe()
async def create_product_inline(
    file: UploadFile,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    category: str | None = Form(None),
    is_bottle: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Quick-create a new SKU with a reference image from the camera.

    Used when a scanned box (or, with ``is_bottle``, a loose bottle) is not
    recognized.
    """
    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' already exists")

    # Photo-matched product: identified via vision, never a barcode. Set this
    # explicitly because the model default is now "barcode".
    sku = SKU(
        sku_code=sku_code, name=name, description=description,
        category=category, is_bottle=is_bottle, product_type="vision",
    )
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

    if not is_package and not is_bottle:
        db.rollback()
        raise HTTPException(400, "Dit is geen doos of verpakking — upload alleen foto's van dozen")

    if embedding is None:
        # Bottle product admitted past the gate: embed the description here.
        embedding = await generate_embedding(vision_description)

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
    db.flush()
    recompute_active(sku, db)
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
    response: Response,
    supplier_code: str = Form(...),
    description: str | None = Form(None),
    is_bottle: bool = Form(False),
    organization_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Create an inactive concept product to be completed by merchant/admin."""
    code = supplier_code.strip().upper()
    if not code:
        raise HTTPException(400, "supplier_code is verplicht")

    if organization_id is not None:
        if not db.get(Organization, organization_id):
            raise HTTPException(404, "Organisatie niet gevonden")
        if (
            user.role in ("owner", "member")
            and organization_id != user.organization_id
        ):
            raise HTTPException(403, "Geen toegang tot deze organisatie")
        target_org_id: int | None = organization_id
    else:
        target_org_id = user.organization_id

    # Bottles are shared with the advice app, which owns their identity through
    # source_product_id. A concept invents a local identity instead, so the feed
    # would later add its own SKU for the same wine and split the stock — with
    # the booked bottles sitting on the copy the advice app cannot see. Boxes
    # stay a Dockscan-only stream and are unaffected.
    if (
        is_bottle
        and settings.has_advice_products_feed(target_org_id)
    ):
        raise HTTPException(
            400,
            "Flesproducten komen uit de advies-app. Maak de wijn daar aan en "
            "gebruik 'Synchroniseer nu' om hem hier op te halen.",
        )

    base_query = db.query(SKU).filter(SKU.sku_code == code)

    def _get_visible_existing() -> SKU | None:
        if target_org_id is not None:
            return base_query.filter(SKU.organization_id == target_org_id).first()
        return base_query.filter(SKU.organization_id.is_(None)).first()

    existing = _get_visible_existing()
    if existing:
        response.status_code = 200
        return _sku_to_response(existing)

    # A SKU with this code exists but is not visible to the current user
    other_org_sku = base_query.first()
    if other_org_sku:
        raise HTTPException(status_code=409, detail="SKU with this code exists in another organization")

    concept_name = (description or "").strip() or f"Concept {code}"
    # A concept is an unfinished wine, not a generic product: it stays inactive
    # until the merchant fills in the wine attributes. category="wine" makes the
    # editor show the wine fields directly and makes is_complete require them.
    sku = SKU(
        sku_code=code,
        name=concept_name,
        description=concept_name,
        category="wine",
        active=False,
        is_bottle=is_bottle,
        organization_id=target_org_id,
        # An unfinished wine is a vision product; the model default is "barcode".
        product_type="vision",
    )
    sku.set_attributes({"status": "concept", "source": "inbound_scan"})
    db.add(sku)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Another concurrent request created the same SKU; return it if visible
        existing = _get_visible_existing()
        if existing:
            response.status_code = 200
            return _sku_to_response(existing)
        raise HTTPException(status_code=409, detail="SKU code conflict")
    db.refresh(sku)

    publish_event(
        "concept_product_created",
        details={"sku_code": sku.sku_code, "name": sku.name, "source": "inbound_scan"},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )

    return _sku_to_response(sku)
