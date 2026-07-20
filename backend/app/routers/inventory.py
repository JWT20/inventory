import hashlib
import logging

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.auth import (
    get_current_user,
    require_inbound_booker,
    require_merchant_inbound,
    require_product_manager,
)
from app.database import get_db
from app.events import publish_event
from app.models import (
    SKU,
    SKUAttribute,
    Customer,
    CustomerSKU,
    InboundUploadAttempt,
    InboundShipment,
    InboundShipmentLine,
    InventoryBalance,
    ReferenceImage,
    Organization,
    Supplier,
    SupplierSKUMapping,
    StockMovement,
    User,
)
from app.schemas import (
    ConfirmLineMatchRequest,
    CustomerPriceResponse,
    InventoryAdjustRequest,
    InventoryBalanceResponse,
    InventoryCountRequest,
    InventoryOverviewItem,
    InboundUploadAttemptResponse,
    SupplierMappingResponse,
    ShipmentCreate,
    ShipmentMatchCandidate,
    ShipmentExtractPreviewResponse,
    ShipmentExtractedLine,
    ShipmentLineResponse,
    ShipmentResponse,
    ShipmentTextExtractRequest,
    StockMovementResponse,
    UpdateCustomerPriceRequest,
    UpdateCustomerSKUDiscountRequest,
    UpdateDefaultPriceRequest,
)
from langfuse import observe, propagate_attributes

from app.services.embedding import (
    extract_shipment_document,
    extract_shipment_text,
    match_shipment_article_name,
)
from app.services.langfuse_client import PromptUnavailableError
from app.services.pricing import calc_effective_price
from app.services.inventory_sync import push_inventory_to_shopify
from app.services.stock import apply_stock_movement
from app.services.storage import storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inventory"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_supplier_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().split()).upper()


def _normalize_supplier_code(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().upper()


LLM_ARTICLE_MATCH_MIN_CONFIDENCE = 0.80

BOTTLES_PER_BOX = 6

_BOX_UNIT_ALIASES = {"boxes", "box", "doos", "dozen", "colli", "kisten", "ds", "ct"}
_PIECE_UNIT_ALIASES = {"pieces", "piece", "bottles", "bottle", "flessen", "fles", "fl", "btls", "stuks", "stuk", "pcs"}


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _resolve_inbound_quantity(row: dict, is_bottle: bool = False) -> tuple[int, int, str]:
    """Normalize LLM quantity output to (booked_units, raw_quantity, unit).

    Rules for box SKUs (default):
    - unit=boxes → use as-is.
    - unit=pieces → convert to boxes using BOTTLES_PER_BOX; partial box (<6) → 0.
    - unit unknown/missing → fall back to legacy quantity_boxes field if present,
      else 0 with unit='unknown' so the operator must confirm.

    Rules for bottle SKUs (is_bottle=True) — the order unit is a single bottle:
    - unit=pieces → count one-to-one (no division by 6).
    - unit=boxes/colli → ambiguous for a bottle product → unit='unknown' so the
      operator confirms the intended number of bottles.
    """
    unit_raw = str(row.get("quantity_unit") or "").strip().lower()
    qty_raw = _to_int(row.get("quantity"), 0)
    legacy_boxes = _to_int(row.get("quantity_boxes"), 0)

    if is_bottle:
        if unit_raw in _PIECE_UNIT_ALIASES:
            qty_raw = max(0, qty_raw)
            return (qty_raw, qty_raw, "pieces")
        return (0, max(qty_raw, legacy_boxes, 0), "unknown")

    if unit_raw in _BOX_UNIT_ALIASES:
        return (max(0, qty_raw), max(0, qty_raw), "boxes")
    if unit_raw in _PIECE_UNIT_ALIASES:
        qty_raw = max(0, qty_raw)
        return (qty_raw // BOTTLES_PER_BOX, qty_raw, "pieces")
    if legacy_boxes > 0:
        return (legacy_boxes, legacy_boxes, "boxes")
    if qty_raw > 0:
        return (0, qty_raw, "unknown")
    return (0, 0, "unknown")


# ---------------------------------------------------------------------------
# Shipment endpoints (pakbon)
# ---------------------------------------------------------------------------

def _shipment_to_response(shipment: InboundShipment) -> ShipmentResponse:
    return ShipmentResponse(
        id=shipment.id,
        organization_id=shipment.organization_id,
        supplier_name=shipment.supplier_name,
        reference=shipment.reference,
        status=shipment.status,
        created_at=shipment.created_at,
        booked_at=shipment.booked_at,
        booked_by=shipment.booked_by,
        lines=[
            ShipmentLineResponse(
                id=line.id,
                sku_id=line.sku_id,
                sku_code=line.sku.sku_code if line.sku else "",
                sku_name=line.sku.name if line.sku else "",
                supplier_code=line.supplier_code,
                quantity=line.quantity,
            )
            for line in shipment.lines
        ],
    )


def _mapping_to_response(mapping: SupplierSKUMapping) -> SupplierMappingResponse:
    return SupplierMappingResponse(
        id=mapping.id,
        organization_id=mapping.organization_id,
        supplier_name=mapping.supplier_name,
        supplier_code=mapping.supplier_code,
        sku_id=mapping.sku_id,
        sku_code=mapping.sku.sku_code if mapping.sku else "",
        sku_name=mapping.sku.name if mapping.sku else "",
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _resolve_org_id_for_user(user: User, requested_org_id: int | None = None) -> int | None:
    if user.is_platform_admin or user.role == "courier":
        return requested_org_id
    return user.organization_id


def _resolve_inventory_org_id(
    db: Session,
    user: User,
    requested_org_id: int | None = None,
) -> int:
    """Resolve the merchant scope for inventory reads.

    Platform admins and couriers operate across merchants and must pick one
    explicitly. Owner/member users are scoped to their own merchant.
    """
    if user.is_platform_admin or user.role == "courier":
        if not requested_org_id:
            raise HTTPException(400, "organization_id is verplicht voor deze voorraadweergave")
        if not db.get(Organization, requested_org_id):
            raise HTTPException(404, "Organisatie niet gevonden")
        return requested_org_id

    if user.role in ("owner", "member") and user.organization_id:
        if requested_org_id and requested_org_id != user.organization_id:
            raise HTTPException(403, "Geen toegang tot deze organisatie")
        return user.organization_id

    raise HTTPException(403, "Geen toegang tot voorraad")


def _upsert_supplier_mapping(
    db: Session,
    *,
    organization_id: int | None,
    supplier_name: str,
    supplier_code: str,
    sku_id: int,
) -> None:
    existing_mapping = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == organization_id,
            SupplierSKUMapping.supplier_name == supplier_name,
            SupplierSKUMapping.supplier_code == supplier_code,
        )
        .first()
    )
    if existing_mapping:
        existing_mapping.sku_id = sku_id
        return

    try:
        with db.begin_nested():
            db.add(SupplierSKUMapping(
                organization_id=organization_id,
                supplier_name=supplier_name,
                supplier_code=supplier_code,
                sku_id=sku_id,
            ))
    except IntegrityError:
        concurrent_mapping = (
            db.query(SupplierSKUMapping)
            .filter(
                SupplierSKUMapping.organization_id == organization_id,
                SupplierSKUMapping.supplier_name == supplier_name,
                SupplierSKUMapping.supplier_code == supplier_code,
            )
            .first()
        )
        if concurrent_mapping:
            concurrent_mapping.sku_id = sku_id
        else:
            raise


async def _build_preview_lines(
    db: Session,
    user: User,
    extracted: dict,
    supplier_name_form: str,
) -> list[ShipmentExtractedLine]:
    """Resolve extracted rows into SKU-matched preview lines for the user's org.

    Shared by the image/PDF and pasted-text extraction endpoints so all inputs
    get identical supplier-mapping lookup, LLM article matching and pieces→boxes
    conversion. Always scoped to ``user.organization_id``.
    """
    target_org_id = user.organization_id
    extracted_supplier = str(extracted.get("supplier_name", "") or "")
    normalized_supplier = (
        _normalize_supplier_name(supplier_name_form)
        or _normalize_supplier_name(extracted_supplier)
    )

    mapping_lookup: dict[tuple[str, str], tuple[int, str, str]] = {}
    sku_candidates: dict[str, tuple[int, str, str]] = {}
    supplier_scoped_candidates: dict[str, tuple[int, str, str]] = {}
    is_bottle_by_id: dict[int, bool] = {}
    if normalized_supplier:
        mappings = db.query(SupplierSKUMapping, SKU).join(
            SKU, SKU.id == SupplierSKUMapping.sku_id
        )
        if target_org_id is not None:
            mappings = mappings.filter(
                SupplierSKUMapping.organization_id == target_org_id
            )
        for mapping, sku in mappings.filter(
            SupplierSKUMapping.supplier_name == normalized_supplier
        ).all():
            normalized_mapping = (sku.id, sku.sku_code, sku.name)
            mapping_lookup[
                (_normalize_supplier_name(mapping.supplier_name), _normalize_supplier_code(mapping.supplier_code))
            ] = normalized_mapping
            supplier_scoped_candidates[_normalize_supplier_code(sku.sku_code)] = normalized_mapping
            is_bottle_by_id[sku.id] = sku.is_bottle

    sku_query = db.query(SKU)
    if target_org_id is not None:
        sku_query = sku_query.filter(SKU.organization_id == target_org_id)
    else:
        sku_query = sku_query.filter(SKU.organization_id.is_(None))
    for sku in sku_query.all():
        sku_candidates[_normalize_supplier_code(sku.sku_code)] = (sku.id, sku.sku_code, sku.name)
        is_bottle_by_id[sku.id] = sku.is_bottle

    lines: list[ShipmentExtractedLine] = []
    for row in extracted.get("lines", []):
        row_dict = row if isinstance(row, dict) else {}
        code = str(row_dict.get("supplier_code", "")).strip()
        qty, qty_raw, qty_unit = _resolve_inbound_quantity(row_dict)
        confidence = float(row_dict.get("confidence", 0.0) or 0.0)

        matched_id = None
        matched_code = None
        matched_name = None
        # Flag any no-code line for human review. Also flag when the LLM
        # could not determine the unit (pieces vs. boxes), so the operator
        # verifies the quantity before booking.
        needs_confirmation = (not code) or qty_unit == "unknown"
        match_source = "unresolved"
        candidate_matches: list[ShipmentMatchCandidate] = []
        if code:
            # Resolution priority: supplier-specific mapping when supplier code exists
            hit = mapping_lookup.get((normalized_supplier, _normalize_supplier_code(code)))
            if hit:
                matched_id, matched_code, matched_name = hit
                match_source = "supplier_mapping"

        # If supplier code is missing, use an LLM-only resolver on article description.
        if not matched_id and not code and str(row_dict.get("description", "")).strip():
            llm_candidate_pool = supplier_scoped_candidates or sku_candidates
            supplier_name_for_matcher = normalized_supplier or "(unknown)"
            suggested_code, llm_confidence = await match_shipment_article_name(
                supplier_name=supplier_name_for_matcher,
                article_description=str(row_dict.get("description", "")).strip(),
                candidates=[(v[1], v[2]) for v in llm_candidate_pool.values()],
            )
            normalized_suggested_code = _normalize_supplier_code(suggested_code)
            if (
                normalized_suggested_code
                and normalized_suggested_code in llm_candidate_pool
            ):
                c_id, c_code, c_name = llm_candidate_pool[normalized_suggested_code]
                candidate_matches = [ShipmentMatchCandidate(
                    sku_id=c_id,
                    sku_code=c_code,
                    sku_name=c_name,
                    is_bottle=is_bottle_by_id.get(c_id, False),
                    confidence=llm_confidence,
                )]
                needs_confirmation = True
                match_source = "llm_suggestion"
                if llm_confidence >= LLM_ARTICLE_MATCH_MIN_CONFIDENCE:
                    confidence = max(confidence, llm_confidence)

        # Re-resolve the quantity once the matched SKU's unit is known: a
        # bottle SKU counts pieces one-to-one and flags box quantities as
        # ambiguous instead of dividing by BOTTLES_PER_BOX.
        if matched_id is not None and is_bottle_by_id.get(matched_id):
            qty, qty_raw, qty_unit = _resolve_inbound_quantity(row_dict, is_bottle=True)
            if qty_unit == "unknown":
                needs_confirmation = True

        lines.append(ShipmentExtractedLine(
            supplier_code=code,
            description=str(row_dict.get("description", "")).strip(),
            quantity_boxes=max(0, qty),
            quantity=max(0, qty_raw),
            quantity_unit=qty_unit,
            confidence=max(0.0, min(confidence, 1.0)),
            matched_sku_id=matched_id,
            matched_sku_code=matched_code,
            matched_sku_name=matched_name,
            is_bottle=bool(matched_id is not None and is_bottle_by_id.get(matched_id)),
            needs_confirmation=needs_confirmation,
            match_source=match_source,
            candidate_matches=candidate_matches,
        ))
    return lines


def _find_duplicate_shipment(db: Session, org_id: int | None, document_sha256: str):
    return (
        db.query(InboundShipment)
        .filter(
            InboundShipment.organization_id == org_id,
            InboundShipment.document_sha256 == document_sha256,
        )
        .order_by(InboundShipment.created_at.desc())
        .first()
    )


def _create_upload_attempt(
    db: Session,
    user: User,
    *,
    source_type: str,
    original_filename: str | None = None,
) -> InboundUploadAttempt:
    attempt = InboundUploadAttempt(
        organization_id=user.organization_id,
        uploaded_by=user.id,
        source_type=source_type,
        original_filename=(original_filename or "").strip()[:255] or None,
        status="processing",
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _fail_upload_attempt(
    db: Session,
    attempt_id: int,
    *,
    stage: str,
    error: Exception,
) -> None:
    """Persist a safe failure summary without masking the original error."""
    try:
        db.rollback()
        attempt = db.get(InboundUploadAttempt, attempt_id)
        if not attempt:
            return
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        attempt.status = "failed"
        attempt.error_stage = stage
        attempt.error_message = str(detail or error.__class__.__name__)[:500]
        attempt.updated_at = _utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to persist inbound upload failure", exc_info=True)


def sweep_stale_inbound_uploads(db: Session) -> int:
    """Mark processing attempts abandoned by a crash or deploy as failed."""
    stale = (
        db.query(InboundUploadAttempt)
        .filter(
            InboundUploadAttempt.status == "processing",
            InboundUploadAttempt.updated_at < _utcnow() - timedelta(minutes=15),
        )
        .all()
    )
    for attempt in stale:
        attempt.status = "failed"
        attempt.error_stage = "extraction"
        attempt.error_message = "Verwerking onderbroken"
        attempt.updated_at = _utcnow()
    if stale:
        db.commit()
    return len(stale)


@router.get("/inbound-uploads", response_model=list[InboundUploadAttemptResponse])
def list_inbound_uploads(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Return the current merchant's most recent inbound upload attempts."""
    query = db.query(InboundUploadAttempt)
    if not user.is_platform_admin:
        query = query.filter(InboundUploadAttempt.organization_id == user.organization_id)
    return (
        query.order_by(InboundUploadAttempt.created_at.desc(), InboundUploadAttempt.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("/shipments/extract-preview", response_model=ShipmentExtractPreviewResponse)
@observe()
async def extract_shipment_preview(
    file: UploadFile = File(...),
    supplier_name: str = Form(""),
    document_type: str = Form("unknown"),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Extraction preview for an uploaded pakbon/factuur (image or PDF)."""
    attempt = _create_upload_attempt(
        db,
        user,
        source_type="file",
        original_filename=file.filename,
    )
    with propagate_attributes(
        user_id=str(user.id),
        metadata={
            "endpoint": "/api/shipments/extract-preview",
            "username": user.username,
            "upload_attempt_id": attempt.id,
        },
    ):
        try:
            file_bytes = file.file.read()
            if not file_bytes:
                raise HTTPException(400, "Leeg bestand")
            if len(file_bytes) > 20 * 1024 * 1024:
                raise HTTPException(413, "Bestand te groot (max 20 MB)")

            document_sha256 = hashlib.sha256(file_bytes).hexdigest()
            duplicate_shipment = _find_duplicate_shipment(db, user.organization_id, document_sha256)

            is_pdf = file_bytes[:1024].find(b"%PDF-") != -1
            ext = "pdf" if is_pdf else "jpg"
            image_key = f"shipment_docs/{uuid.uuid4().hex}.{ext}"
            storage.save(image_key, file_bytes)
            attempt.document_sha256 = document_sha256
            attempt.document_key = image_key
            attempt.updated_at = _utcnow()
            db.commit()

            extracted = await extract_shipment_document(file_bytes)
            detected_type = extracted.get("document_type") or "unknown"
            if document_type in {"pakbon", "invoice"}:
                detected_type = document_type

            lines = await _build_preview_lines(db, user, extracted, supplier_name)
            resolved_supplier = (
                supplier_name.strip()
                or str(extracted.get("supplier_name", "") or "").strip()
            )
            reference = str(extracted.get("reference", "") or "")
            attempt.supplier_name = resolved_supplier or None
            attempt.reference = reference or None
            attempt.status = "needs_action"
            attempt.line_count = len(lines)
            attempt.bookable_line_count = sum(
                1 for line in lines if line.matched_sku_id and line.quantity_boxes > 0
            )
            attempt.error_stage = None
            attempt.error_message = None
            attempt.updated_at = _utcnow()
            db.commit()

            return ShipmentExtractPreviewResponse(
                supplier_name=resolved_supplier,
                reference=reference,
                document_type=detected_type,
                lines=lines,
                image_url=("" if is_pdf else storage.url(image_key)),
                raw_text=str(extracted.get("raw_text", "") or ""),
                upload_attempt_id=attempt.id,
                document_sha256=document_sha256,
                duplicate_of_shipment_id=(duplicate_shipment.id if duplicate_shipment else None),
                duplicate_of_status=(duplicate_shipment.status if duplicate_shipment else None),
            )
        except Exception as exc:
            _fail_upload_attempt(db, attempt.id, stage="extraction", error=exc)
            raise


@router.post("/shipments/extract-preview-text", response_model=ShipmentExtractPreviewResponse)
@observe()
async def extract_shipment_preview_text(
    body: ShipmentTextExtractRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Extraction preview from pasted order text (no file). LLM-only extraction."""
    attempt = _create_upload_attempt(db, user, source_type="text")
    with propagate_attributes(
        user_id=str(user.id),
        metadata={
            "endpoint": "/api/shipments/extract-preview-text",
            "username": user.username,
            "upload_attempt_id": attempt.id,
        },
    ):
        try:
            text = body.text.strip()
            if not text:
                raise HTTPException(400, "Lege tekst")
            if len(text) > 50_000:
                raise HTTPException(413, "Tekst te lang (max 50.000 tekens)")

            # Hash normalized text so an accidental re-paste of the same order is
            # flagged (soft warning), scoped per merchant via organization_id.
            normalized_text = " ".join(text.split()).lower()
            document_sha256 = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
            duplicate_shipment = _find_duplicate_shipment(db, user.organization_id, document_sha256)
            attempt.document_sha256 = document_sha256
            attempt.updated_at = _utcnow()
            db.commit()

            extracted = await extract_shipment_text(text)
        except PromptUnavailableError as exc:
            _fail_upload_attempt(db, attempt.id, stage="extraction", error=exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Tekst-extractie is niet beschikbaar: de vereiste Langfuse-prompt "
                    "'extract-shipment-text' kon niet worden opgehaald."
                ),
            ) from exc
        except Exception as exc:
            _fail_upload_attempt(db, attempt.id, stage="extraction", error=exc)
            raise

        try:
            detected_type = extracted.get("document_type") or "unknown"
            if body.document_type in {"pakbon", "invoice"}:
                detected_type = body.document_type

            lines = await _build_preview_lines(db, user, extracted, body.supplier_name)
            resolved_supplier = (
                body.supplier_name.strip()
                or str(extracted.get("supplier_name", "") or "").strip()
            )
            reference = str(extracted.get("reference", "") or "")
            attempt.supplier_name = resolved_supplier or None
            attempt.reference = reference or None
            attempt.status = "needs_action"
            attempt.line_count = len(lines)
            attempt.bookable_line_count = sum(
                1 for line in lines if line.matched_sku_id and line.quantity_boxes > 0
            )
            attempt.error_stage = None
            attempt.error_message = None
            attempt.updated_at = _utcnow()
            db.commit()

            return ShipmentExtractPreviewResponse(
                supplier_name=resolved_supplier,
                reference=reference,
                document_type=detected_type,
                lines=lines,
                image_url="",
                raw_text=str(extracted.get("raw_text", "") or ""),
                upload_attempt_id=attempt.id,
                document_sha256=document_sha256,
                duplicate_of_shipment_id=(duplicate_shipment.id if duplicate_shipment else None),
                duplicate_of_status=(duplicate_shipment.status if duplicate_shipment else None),
            )
        except Exception as exc:
            _fail_upload_attempt(db, attempt.id, stage="extraction", error=exc)
            raise


@router.post("/shipments", response_model=ShipmentResponse, status_code=201)
def create_shipment(
    data: ShipmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Create a new inbound shipment (pakbon) with lines."""
    sku_ids = [line.sku_id for line in data.lines]
    existing_skus = db.query(SKU.id).filter(SKU.id.in_(sku_ids)).all()
    existing_ids = {row[0] for row in existing_skus}
    missing = set(sku_ids) - existing_ids
    if missing:
        raise HTTPException(400, f"SKU's niet gevonden: {missing}")

    if user.organization_id:
        org_id = user.organization_id
    else:
        raise HTTPException(400, "User has no organization")

    upload_attempt = None
    if data.upload_attempt_id:
        upload_attempt = (
            db.query(InboundUploadAttempt)
            .filter(
                InboundUploadAttempt.id == data.upload_attempt_id,
                InboundUploadAttempt.organization_id == org_id,
            )
            .with_for_update()
            .first()
        )
        if not upload_attempt:
            raise HTTPException(404, "Uploadpoging niet gevonden")
        if upload_attempt.shipment_id:
            raise HTTPException(409, "Deze uploadpoging is al opgeslagen")
        if (
            upload_attempt.document_sha256
            and data.document_sha256
            and upload_attempt.document_sha256 != data.document_sha256
        ):
            raise HTTPException(400, "Document komt niet overeen met de uploadpoging")

    normalized_supplier_name = _normalize_supplier_name(data.supplier_name)
    supplier_name_display = data.supplier_name.strip() if data.supplier_name else None

    reference_value = (data.reference or "").strip() or None
    if reference_value and not data.force:
        existing = (
            db.query(InboundShipment)
            .filter(
                InboundShipment.organization_id == org_id,
                InboundShipment.supplier_name == supplier_name_display,
                InboundShipment.reference == reference_value,
                InboundShipment.status != "cancelled",
            )
            .order_by(InboundShipment.created_at.desc())
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_pakbon",
                    "message": (
                        f"Pakbon '{reference_value}' van leverancier "
                        f"'{supplier_name_display or '(onbekend)'}' bestaat al."
                    ),
                    "existing_shipment_id": existing.id,
                    "existing_status": existing.status,
                },
            )

    if reference_value and data.force:
        suffix = 2
        candidate = f"{reference_value}-dup-{suffix}"
        while (
            db.query(InboundShipment.id)
            .filter(
                InboundShipment.organization_id == org_id,
                InboundShipment.supplier_name == supplier_name_display,
                InboundShipment.reference == candidate,
            )
            .first()
            is not None
        ):
            suffix += 1
            candidate = f"{reference_value}-dup-{suffix}"
        reference_value = candidate

    shipment = InboundShipment(
        organization_id=org_id,
        supplier_name=supplier_name_display,
        reference=reference_value,
        status="draft",
        document_sha256=data.document_sha256,
    )
    db.add(shipment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(InboundShipment)
            .filter(
                InboundShipment.organization_id == org_id,
                InboundShipment.supplier_name == supplier_name_display,
                InboundShipment.reference == reference_value,
                InboundShipment.status != "cancelled",
            )
            .order_by(InboundShipment.created_at.desc())
            .first()
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_pakbon",
                "message": "Pakbon bestaat al.",
                "existing_shipment_id": existing.id if existing else None,
                "existing_status": existing.status if existing else None,
            },
        )

    if upload_attempt:
        upload_attempt.shipment_id = shipment.id
        upload_attempt.supplier_name = supplier_name_display
        upload_attempt.reference = reference_value
        upload_attempt.status = "draft"
        upload_attempt.bookable_line_count = len(data.lines)
        upload_attempt.error_stage = None
        upload_attempt.error_message = None
        upload_attempt.updated_at = _utcnow()

    for line in data.lines:
        db.add(InboundShipmentLine(
            shipment_id=shipment.id,
            sku_id=line.sku_id,
            supplier_code=_normalize_supplier_code(line.supplier_code) or None,
            quantity=line.quantity,
        ))
        if normalized_supplier_name and line.supplier_code:
            normalized_code = _normalize_supplier_code(line.supplier_code)
            if normalized_code:
                _upsert_supplier_mapping(
                    db,
                    organization_id=org_id,
                    supplier_name=normalized_supplier_name,
                    supplier_code=normalized_code,
                    sku_id=line.sku_id,
                )
    db.commit()
    db.refresh(shipment)

    publish_event(
        "shipment_created",
        details={
            "shipment_id": shipment.id,
            "reference": shipment.reference,
            "line_count": len(data.lines),
        },
        user=user,
        resource_type="shipment",
        resource_id=shipment.id,
    )

    return _shipment_to_response(shipment)


@router.get("/supplier-mappings", response_model=list[SupplierMappingResponse])
def list_supplier_mappings(
    supplier_name: str | None = None,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    org_id = _resolve_org_id_for_user(user, organization_id)
    query = db.query(SupplierSKUMapping).options(joinedload(SupplierSKUMapping.sku))
    if org_id is None and not user.is_platform_admin:
        return []
    query = query.filter(SupplierSKUMapping.organization_id == org_id)
    if supplier_name:
        query = query.filter(
            SupplierSKUMapping.supplier_name == _normalize_supplier_name(supplier_name)
        )
    rows = query.order_by(
        SupplierSKUMapping.supplier_name.asc(),
        SupplierSKUMapping.supplier_code.asc(),
    ).all()
    return [_mapping_to_response(row) for row in rows]


@router.delete("/supplier-mappings/{mapping_id}", status_code=204)
def delete_supplier_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    mapping = db.get(SupplierSKUMapping, mapping_id)
    if not mapping:
        raise HTTPException(404, "Mapping niet gevonden")
    if not user.is_platform_admin and mapping.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang tot deze mapping")
    db.delete(mapping)
    db.commit()
    return Response(status_code=204)


@router.post("/shipments/confirm-line-match", response_model=SupplierMappingResponse)
def confirm_line_match(
    body: ConfirmLineMatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    org_id = user.organization_id
    if not user.is_platform_admin and not org_id:
        raise HTTPException(400, "User has no organization")
    sku = db.get(SKU, body.chosen_sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")
    if not user.is_platform_admin and sku.organization_id != org_id:
        raise HTTPException(403, "Geen toegang tot deze SKU")

    normalized_supplier_name = _normalize_supplier_name(body.supplier_name)
    normalized_supplier_code = _normalize_supplier_code(body.supplier_code)
    if not normalized_supplier_name or not normalized_supplier_code:
        missing = []
        if not normalized_supplier_name:
            missing.append("supplier_name")
        if not normalized_supplier_code:
            missing.append("supplier_code")
        raise HTTPException(
            status_code=422,
            detail=f"Field(s) must be non-empty after normalization: {', '.join(missing)}",
        )
    if body.persist_mapping:
        _upsert_supplier_mapping(
            db,
            organization_id=org_id,
            supplier_name=normalized_supplier_name,
            supplier_code=normalized_supplier_code,
            sku_id=sku.id,
        )
        db.commit()

    mapping = (
        db.query(SupplierSKUMapping)
        .options(joinedload(SupplierSKUMapping.sku))
        .filter(
            SupplierSKUMapping.organization_id == org_id,
            SupplierSKUMapping.supplier_name == normalized_supplier_name,
            SupplierSKUMapping.supplier_code == normalized_supplier_code,
        )
        .first()
    )
    if not mapping:
        return SupplierMappingResponse(
            id=None,
            organization_id=org_id,
            supplier_name=normalized_supplier_name,
            supplier_code=normalized_supplier_code,
            sku_id=sku.id,
            sku_code=sku.sku_code,
            sku_name=sku.name,
            created_at=None,
            updated_at=None,
        )
    return _mapping_to_response(mapping)


@router.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        db.query(InboundShipment)
        .options(joinedload(InboundShipment.lines).joinedload(InboundShipmentLine.sku))
    )
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(InboundShipment.organization_id == user.organization_id)
        else:
            return []
    shipments = query.order_by(InboundShipment.created_at.desc()).all()
    return [_shipment_to_response(s) for s in shipments]


@router.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    shipment = (
        db.query(InboundShipment)
        .options(joinedload(InboundShipment.lines).joinedload(InboundShipmentLine.sku))
        .filter(InboundShipment.id == shipment_id)
        .first()
    )
    if not shipment:
        raise HTTPException(404, "Pakbon niet gevonden")
    if not user.is_platform_admin and shipment.organization_id != user.organization_id:
        raise HTTPException(404, "Pakbon niet gevonden")
    return _shipment_to_response(shipment)


@router.post("/shipments/{shipment_id}/book", response_model=ShipmentResponse)
def book_shipment(
    shipment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Book a shipment: create stock movements for all lines and update balances."""
    shipment = (
        db.query(InboundShipment)
        .filter(InboundShipment.id == shipment_id)
        .with_for_update()
        .first()
    )
    if not shipment:
        raise HTTPException(404, "Pakbon niet gevonden")
    if not user.is_platform_admin and shipment.organization_id != user.organization_id:
        raise HTTPException(404, "Pakbon niet gevonden")
    if shipment.status != "draft":
        raise HTTPException(400, "Pakbon is al geboekt")

    upload_attempt = (
        db.query(InboundUploadAttempt)
        .filter(InboundUploadAttempt.shipment_id == shipment.id)
        .with_for_update()
        .first()
    )
    try:
        for line in shipment.lines:
            apply_stock_movement(
                db,
                sku_id=line.sku_id,
                organization_id=shipment.organization_id,
                quantity=line.quantity,
                movement_type="receive",
                reference_type="shipment",
                reference_id=shipment.id,
                performed_by=user.id,
            )

        shipment.status = "booked"
        shipment.booked_at = func.now()
        shipment.booked_by = user.id
        if upload_attempt:
            upload_attempt.status = "booked"
            upload_attempt.booked_line_count = len(shipment.lines)
            upload_attempt.booked_quantity = sum(line.quantity for line in shipment.lines)
            upload_attempt.error_stage = None
            upload_attempt.error_message = None
            upload_attempt.updated_at = _utcnow()
        db.commit()
    except Exception as exc:
        if upload_attempt:
            _fail_upload_attempt(db, upload_attempt.id, stage="booking", error=exc)
        else:
            db.rollback()
        raise

    db.refresh(shipment)

    # Mirror each affected product's new available to Shopify. Dedupe: a pakbon
    # may carry several lines for the same SKU, but one push per SKU suffices.
    for pushed_sku_id in {line.sku_id for line in shipment.lines}:
        background_tasks.add_task(
            push_inventory_to_shopify, pushed_sku_id, shipment.organization_id
        )

    publish_event(
        "shipment_booked",
        details={
            "shipment_id": shipment.id,
            "reference": shipment.reference,
            "line_count": len(shipment.lines),
            "total_quantity": sum(l.quantity for l in shipment.lines),
        },
        user=user,
        resource_type="shipment",
        resource_id=shipment.id,
    )

    return _shipment_to_response(shipment)


@router.delete("/shipments/{shipment_id}", status_code=204)
def delete_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant_inbound),
):
    """Delete a draft shipment. Booked shipments cannot be deleted."""
    shipment = db.query(InboundShipment).filter(InboundShipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(404, "Pakbon niet gevonden")
    if not user.is_platform_admin and shipment.organization_id != user.organization_id:
        raise HTTPException(404, "Pakbon niet gevonden")
    if shipment.status != "draft":
        raise HTTPException(
            409, "Kan een geboekte pakbon niet verwijderen — alleen drafts kunnen verwijderd worden"
        )

    reference = shipment.reference
    upload_attempt = db.query(InboundUploadAttempt).filter(
        InboundUploadAttempt.shipment_id == shipment.id
    ).first()
    if upload_attempt:
        upload_attempt.shipment_id = None
        upload_attempt.status = "failed"
        upload_attempt.error_stage = "shipment"
        upload_attempt.error_message = "Conceptshipment verwijderd"
        upload_attempt.updated_at = _utcnow()
    db.delete(shipment)
    db.commit()
    publish_event(
        "shipment_deleted",
        details={"shipment_id": shipment_id, "reference": reference},
        user=user,
        resource_type="shipment",
        resource_id=shipment_id,
    )


# ---------------------------------------------------------------------------
# Inventory endpoints
# ---------------------------------------------------------------------------

@router.get("/inventory", response_model=list[InventoryBalanceResponse])
def list_inventory(
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org_id = _resolve_inventory_org_id(db, user, organization_id)
    query = (
        db.query(InventoryBalance)
        .join(SKU, InventoryBalance.sku_id == SKU.id)
        .filter(InventoryBalance.organization_id == org_id)
    )

    balances = query.order_by(SKU.name).all()
    return [
        InventoryBalanceResponse(
            sku_id=b.sku_id,
            sku_code=b.sku.sku_code,
            sku_name=b.sku.name,
            organization_id=b.organization_id,
            quantity_on_hand=b.quantity_on_hand,
            quantity_reserved=b.quantity_reserved,
            quantity_available=b.quantity_available,
            last_movement_at=b.last_movement_at,
        )
        for b in balances
    ]


@router.get("/inventory/overview", response_model=list[InventoryOverviewItem])
def inventory_overview(
    organization_id: int | None = None,
    search: str | None = None,
    wijntype: str | None = None,
    producent: str | None = None,
    in_stock_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Full inventory overview for merchants: stock, attributes, prices per customer."""
    org_id = _resolve_inventory_org_id(db, user, organization_id)

    # Start from SKU with LEFT JOIN to InventoryBalance so all products show up
    query = (
        db.query(SKU, InventoryBalance)
        .outerjoin(
            InventoryBalance,
            (InventoryBalance.sku_id == SKU.id)
            & (InventoryBalance.organization_id == org_id),
        )
        .options(
            selectinload(SKU.attributes),
        )
        .filter(SKU.active.is_(True))
        .filter(SKU.organization_id == org_id)
    )

    if in_stock_only:
        query = query.filter(
            (InventoryBalance.quantity_on_hand - InventoryBalance.quantity_reserved) > 0
        )

    if search:
        like = f"%{search}%"
        # Match on product name OR the name of the supplier (leverancier) the
        # wine is sourced from, so typing a supplier name lists all its wines.
        supplier_subq = (
            db.query(Supplier.id)
            .filter(
                Supplier.organization_id == org_id,
                Supplier.name.ilike(like),
            )
        )
        query = query.filter(
            or_(
                SKU.name.ilike(like),
                SKU.supplier_id.in_(supplier_subq),
            )
        )

    if wijntype:
        query = query.filter(
            SKU.id.in_(
                db.query(SKUAttribute.sku_id).filter(
                    SKUAttribute.key == "wijntype",
                    SKUAttribute.value.ilike(f"%{wijntype}%"),
                )
            )
        )

    if producent:
        query = query.filter(
            SKU.id.in_(
                db.query(SKUAttribute.sku_id).filter(
                    SKUAttribute.key == "producent",
                    SKUAttribute.value.ilike(f"%{producent}%"),
                )
            )
        )

    rows = query.order_by(SKU.name).all()

    # Batch-load customer prices for all SKUs in result
    sku_ids = [sku.id for sku, _ in rows]
    image_urls_by_sku: dict[int, str] = {}
    if sku_ids:
        image_rows = (
            db.query(ReferenceImage.sku_id, ReferenceImage.image_path)
            .filter(ReferenceImage.sku_id.in_(sku_ids))
            .filter(ReferenceImage.processing_status == "done")
            .order_by(
                ReferenceImage.sku_id,
                ReferenceImage.created_at,
                ReferenceImage.id,
            )
            .all()
        )
        for sku_id, image_path in image_rows:
            image_urls_by_sku.setdefault(sku_id, f"/api/thumbnails/112/{image_path}")

    can_view_prices = user.role != "courier"
    customer_prices_rows = (
        db.query(CustomerSKU, Customer.name, Customer.discount_percentage)
        .join(Customer, CustomerSKU.customer_id == Customer.id)
        .filter(CustomerSKU.sku_id.in_(sku_ids))
        .all()
    ) if sku_ids and can_view_prices else []

    # Build a lookup of default_price per sku for effective price calculation
    sku_default_prices: dict[int, float | None] = {}
    for sku, _ in rows:
        sku_default_prices[sku.id] = float(sku.default_price) if sku.default_price is not None else None

    # Group by sku_id
    prices_by_sku: dict[int, list[CustomerPriceResponse]] = {}
    for cs, cname, cdiscount in customer_prices_rows:
        unit = float(cs.unit_price) if cs.unit_price is not None else None
        dt = cs.discount_type
        dv = float(cs.discount_value) if cs.discount_value is not None else None
        cpct = float(cdiscount) if cdiscount is not None else None
        effective = calc_effective_price(
            sku_default_prices.get(cs.sku_id), unit, dt, dv, cpct
        )
        prices_by_sku.setdefault(cs.sku_id, []).append(
            CustomerPriceResponse(
                customer_id=cs.customer_id,
                customer_name=cname,
                unit_price=unit,
                discount_type=dt,
                discount_value=dv,
                effective_price=effective,
            )
        )

    result = []
    for sku, balance in rows:
        result.append(
            InventoryOverviewItem(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.name,
                attributes=sku.attributes_dict,
                default_price=(
                    float(sku.default_price)
                    if can_view_prices and sku.default_price is not None
                    else None
                ),
                quantity_on_hand=balance.quantity_on_hand if balance else 0,
                quantity_reserved=balance.quantity_reserved if balance else 0,
                quantity_available=balance.quantity_available if balance else 0,
                last_movement_at=balance.last_movement_at if balance else None,
                image_url=image_urls_by_sku.get(sku.id),
                customer_prices=prices_by_sku.get(sku.id, []),
            )
        )

    return result


@router.put("/skus/{sku_id}/price", response_model=InventoryOverviewItem)
def update_default_price(
    sku_id: int,
    data: UpdateDefaultPriceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")
    if not user.is_platform_admin and sku.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    sku.default_price = data.default_price
    db.commit()
    db.refresh(sku)

    # Return a minimal overview item
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == (user.organization_id or sku.organization_id),
        )
        .first()
    )

    return InventoryOverviewItem(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        attributes=sku.attributes_dict,
        default_price=float(sku.default_price) if sku.default_price is not None else None,
        quantity_on_hand=balance.quantity_on_hand if balance else 0,
        quantity_reserved=balance.quantity_reserved if balance else 0,
        quantity_available=balance.quantity_available if balance else 0,
        last_movement_at=balance.last_movement_at if balance else None,
    )


@router.put("/customers/{customer_id}/skus/{sku_id}/price")
def update_customer_price(
    customer_id: int,
    sku_id: int,
    data: UpdateCustomerPriceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    link = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "Klant-SKU koppeling niet gevonden")

    link.unit_price = data.unit_price
    db.commit()

    return {"ok": True}


@router.put("/customers/{customer_id}/skus/{sku_id}/discount")
def update_customer_sku_discount(
    customer_id: int,
    sku_id: int,
    data: UpdateCustomerSKUDiscountRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    link = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "Klant-SKU koppeling niet gevonden")

    link.discount_type = data.discount_type
    link.discount_value = data.discount_value
    db.commit()

    # Return effective price info
    sku = db.get(SKU, sku_id)
    default_price = float(sku.default_price) if sku and sku.default_price is not None else None
    unit = float(link.unit_price) if link.unit_price is not None else None
    dt = link.discount_type
    dv = float(link.discount_value) if link.discount_value is not None else None
    cpct = (
        float(customer.discount_percentage)
        if customer.discount_percentage is not None
        else None
    )

    return {
        "ok": True,
        "discount_type": dt,
        "discount_value": dv,
        "effective_price": calc_effective_price(default_price, unit, dt, dv, cpct),
    }


@router.get("/inventory/{sku_id}/movements", response_model=list[StockMovementResponse])
def list_movements(
    sku_id: int,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(StockMovement).filter(StockMovement.sku_id == sku_id)
    if user.is_platform_admin:
        if organization_id:
            query = query.filter(StockMovement.organization_id == organization_id)
    elif user.organization_id:
        query = query.filter(StockMovement.organization_id == user.organization_id)
    else:
        return []
    return query.order_by(StockMovement.created_at.desc()).all()


@router.post("/inventory/adjust", response_model=StockMovementResponse)
def adjust_inventory(
    data: InventoryAdjustRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Manual stock adjustment (positive or negative delta)."""
    if data.quantity == 0:
        raise HTTPException(400, "Voorraadaanpassing mag niet 0 zijn")

    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    organization_id = _resolve_inventory_org_id(db, user, data.organization_id)
    if sku.organization_id != organization_id:
        raise HTTPException(403, "Geen toegang tot deze SKU")

    movement = apply_stock_movement(
        db,
        sku_id=data.sku_id,
        organization_id=organization_id,
        quantity=data.quantity,
        movement_type="adjust",
        reference_type="manual",
        note=data.note,
        performed_by=user.id,
    )
    db.commit()
    db.refresh(movement)

    # Mirror the new available to Shopify after the response (best-effort).
    background_tasks.add_task(push_inventory_to_shopify, data.sku_id, organization_id)

    publish_event(
        "inventory_adjusted",
        details={
            "sku_code": sku.sku_code,
            "quantity": data.quantity,
            "note": data.note,
        },
        user=user,
        resource_type="inventory",
        resource_id=movement.id,
    )

    return movement


@router.post("/inventory/count", response_model=StockMovementResponse)
def count_inventory(
    data: InventoryCountRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Physical count: set stock to absolute value by computing delta."""
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    organization_id = _resolve_inventory_org_id(db, user, data.organization_id)
    if sku.organization_id != organization_id:
        raise HTTPException(403, "Geen toegang tot deze SKU")

    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == data.sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .first()
    )
    current = balance.quantity_on_hand if balance else 0
    delta = data.counted_quantity - current

    if delta == 0:
        raise HTTPException(200, "Telling komt overeen met huidige voorraad, geen wijziging nodig")

    movement = apply_stock_movement(
        db,
        sku_id=data.sku_id,
        organization_id=organization_id,
        quantity=delta,
        movement_type="count",
        reference_type="manual",
        note=data.note or f"Telling: {current} → {data.counted_quantity}",
        performed_by=user.id,
    )
    db.commit()
    db.refresh(movement)

    # Mirror the new available to Shopify — a physical count changes the same
    # balance a manual adjust does, so it must push too (otherwise Shopify drifts).
    background_tasks.add_task(push_inventory_to_shopify, data.sku_id, organization_id)

    publish_event(
        "inventory_counted",
        details={
            "sku_code": sku.sku_code,
            "previous": current,
            "counted": data.counted_quantity,
            "delta": delta,
        },
        user=user,
        resource_type="inventory",
        resource_id=movement.id,
    )

    return movement
