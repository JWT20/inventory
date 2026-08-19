import asyncio
import datetime
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy import or_, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_can_edit_photos, require_product_manager
from app.config import settings
from app.database import get_db
from app.events import publish_event
from app.models import (
    SKU,
    Booking,
    InboundShipmentLine,
    InventoryBalance,
    OrderLine,
    Organization,
    ReferenceImage,
    SKUAttribute,
    StockMovement,
    Supplier,
    User,
)
from app.modules import PICKING_MODULE_BY_PRODUCT_TYPE
from app.schemas import (
    WINE_ATTRIBUTE_KEYS,
    AdviceProductSyncResponse,
    ReferenceImageResponse,
    ReferenceImageStatusResponse,
    SKUCreate,
    SKUOption,
    SKUResponse,
    SKUUpdate,
    generate_wine_display_name,
    generate_wine_sku_code,
    is_valid_ean13,
    normalize_ean,
)
from langfuse import observe

from app.services.embedding import (
    UnsupportedImageError,
    assess_description_quality,
    classify_and_describe,
    describe_and_embed,
    generate_embedding,
    normalize_upload_to_jpeg,
)
from PIL import UnidentifiedImageError
from app.services.advice_products import (
    AdviceProductsClient,
    AdviceProductSyncBusy,
    AdviceProductSyncError,
    advice_sync_slot,
    sync_advice_products,
)
from app.services.booking import promote_pending_images_orders_for_sku
from app.services.channel_import import resync_channel_for_new_ean
from app.services.product_status import is_complete, recompute_active
from app.services.storage import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skus", tags=["skus"])

# Threshold for considering two images as duplicates (cosine similarity)
DUPLICATE_SIMILARITY_THRESHOLD = 0.90
WINE_CHECK_FAILED = "wine_check_failed"
DUPLICATE_CHECK_FAILED = "duplicate_check_failed"
PROCESSING_FAILED = "processing_failed"

# Max accepted upload size, enforced on both the raw upload and the normalized
# JPEG (a HEIC can expand when decoded).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Postgres advisory-lock key serializing the duplicate-detection window across
# concurrent background tasks. Arbitrary 32-bit constant ('WINE' = 0x57494E45).
DUPLICATE_LOCK_KEY = 0x57494E45

# A reference image stuck in pending/processing for longer than this is
# considered abandoned (worker died, deploy mid-flight, etc.) and may be
# retried or swept to "failed" on startup.
STALE_PROCESSING_TIMEOUT = datetime.timedelta(minutes=5)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _is_stale(image: ReferenceImage) -> bool:
    if image.processing_status not in ("pending", "processing"):
        return False
    started = image.processing_started_at or image.created_at
    if started is None:
        return True
    return _utcnow() - started > STALE_PROCESSING_TIMEOUT


def _check_duplicate_embedding(
    db: Session, embedding: list[float], exclude_sku_id: int,
) -> tuple[SKU | None, float]:
    """Check if a similar image already exists on a different SKU.

    Returns (matching_sku, similarity) or (None, 0.0).
    """
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    row = db.execute(
        text("""
            SELECT ri.sku_id, 1 - (ri.embedding <=> :embedding) AS similarity
            FROM reference_images ri
            WHERE ri.embedding IS NOT NULL
              AND ri.sku_id != :exclude_sku_id
            ORDER BY ri.embedding <=> :embedding
            LIMIT 1
        """),
        {"embedding": embedding_str, "exclude_sku_id": exclude_sku_id},
    ).first()
    if row and row[1] >= DUPLICATE_SIMILARITY_THRESHOLD:
        sku = db.get(SKU, row[0])
        return sku, float(row[1])
    return None, 0.0


def _clear_processing_error(image: ReferenceImage) -> None:
    image.processing_error_code = None
    image.processing_error_message = None
    image.duplicate_sku_id = None


def _friendly_processing_error(exc: Exception) -> str:
    """Map a processing exception to a user-facing, diagnosable message.

    Known categories get a clean Dutch message; anything unexpected keeps the
    friendly retry text but appends the exception type so the cause is visible
    in the DB/UI without scraping container logs (which rotate on deploy).
    """
    if isinstance(exc, (UnidentifiedImageError, UnsupportedImageError)):
        return (
            "Niet-ondersteund of beschadigd afbeeldingsbestand. "
            "Gebruik een JPG, PNG of HEIC."
        )
    return f"Beeldanalyse is mislukt ({type(exc).__name__}). Probeer het opnieuw."


def _set_processing_failure(
    image: ReferenceImage,
    *,
    code: str,
    message: str,
    duplicate_sku_id: int | None = None,
) -> None:
    image.processing_status = "failed"
    image.processing_error_code = code
    image.processing_error_message = message
    image.duplicate_sku_id = duplicate_sku_id


def _acquire_duplicate_lock(db: Session) -> None:
    """Serialize the duplicate-check + embedding-write window across workers.

    Postgres-only; on other dialects (SQLite in tests) this is a no-op.
    The lock is transaction-scoped and released by the next commit/rollback.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": DUPLICATE_LOCK_KEY},
        )


async def _process_reference_image(
    image_id: int,
    *,
    skip_wine_check: bool,
    skip_duplicate_check: bool,
    bind: Engine,
) -> None:
    db = Session(bind=bind, autocommit=False, autoflush=False)
    try:
        image = db.get(ReferenceImage, image_id)
        if image is None:
            return

        sku = db.get(SKU, image.sku_id)
        if sku is None:
            _set_processing_failure(
                image,
                code=PROCESSING_FAILED,
                message="Product niet gevonden tijdens beeldanalyse",
            )
            db.commit()
            return

        image.processing_status = "processing"
        image.processing_started_at = _utcnow()
        _clear_processing_error(image)
        db.commit()

        image_bytes = storage.read(image.image_path)
        if image_bytes is None:
            raise RuntimeError("Afbeelding kon niet worden gelezen")

        if skip_wine_check:
            description, embedding, quality = await describe_and_embed(image_bytes)
        else:
            is_package, description = await classify_and_describe(image_bytes)
            if not is_package:
                _set_processing_failure(
                    image,
                    code=WINE_CHECK_FAILED,
                    message=(
                        "Dit beeld werd niet herkend als wijndoos of fles "
                        f"({description})."
                    ),
                )
                recompute_active(sku, db)
                db.commit()
                return
            quality = assess_description_quality(description)
            embedding = await generate_embedding(description)

        # Serialize duplicate detection so two near-simultaneous uploads can't
        # both pass the check before either commits its embedding.
        if not skip_duplicate_check:
            _acquire_duplicate_lock(db)
            dup_sku, similarity = _check_duplicate_embedding(
                db, embedding, exclude_sku_id=image.sku_id
            )
            if dup_sku:
                _set_processing_failure(
                    image,
                    code=DUPLICATE_CHECK_FAILED,
                    message=(
                        f"Deze foto lijkt te veel op een foto van {dup_sku.sku_code} "
                        f"(gelijkenis: {similarity:.0%})."
                    ),
                    duplicate_sku_id=dup_sku.id,
                )
                recompute_active(sku, db)
                db.commit()
                return

        image.vision_description = description
        image.embedding = embedding
        image.description_quality = quality
        image.wine_check_overridden = skip_wine_check
        image.processing_status = "done"
        _clear_processing_error(image)
        recompute_active(sku, db)
        db.commit()
    except Exception as exc:
        logger.exception("Reference image processing failed for image %s", image_id)
        db.rollback()
        image = db.get(ReferenceImage, image_id)
        if image is not None:
            sku = db.get(SKU, image.sku_id)
            _set_processing_failure(
                image,
                code=PROCESSING_FAILED,
                message=_friendly_processing_error(exc),
            )
            if sku is not None:
                recompute_active(sku, db)
            db.commit()
    finally:
        db.close()


def sweep_stale_reference_images(db: Session) -> int:
    """Mark abandoned pending/processing rows as failed so users can retry.

    Called on application startup. Returns the number of rows updated.
    """
    cutoff = _utcnow() - STALE_PROCESSING_TIMEOUT
    stale = (
        db.query(ReferenceImage)
        .filter(
            ReferenceImage.processing_status.in_(("pending", "processing")),
            ReferenceImage.created_at < cutoff,
        )
        .all()
    )
    count = 0
    for image in stale:
        if not _is_stale(image):
            continue
        _set_processing_failure(
            image,
            code=PROCESSING_FAILED,
            message="Beeldanalyse onderbroken — probeer opnieuw.",
        )
        count += 1
    if count:
        db.commit()
        logger.info("Reference image sweep: marked %d stale rows as failed", count)
    return count


def _ean_conflict(
    db: Session, organization_id: int | None, ean: str, exclude_sku_id: int | None = None
) -> bool:
    """True if another SKU in the same organization already owns this EAN."""
    q = db.query(SKU).filter(SKU.organization_id == organization_id, SKU.ean == ean)
    if exclude_sku_id is not None:
        q = q.filter(SKU.id != exclude_sku_id)
    return db.query(q.exists()).scalar()


def _normalize_source_product_id(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _validate_source_product_link(
    db: Session,
    *,
    organization_id: int | None,
    source_product_id: str | None,
    is_bottle: bool,
    exclude_sku_id: int | None = None,
) -> None:
    if (
        settings.has_advice_products_feed(organization_id)
        and is_bottle
        and source_product_id is None
    ):
        raise HTTPException(
            400,
            "Flesproducten komen uit de advies-app en vereisen een "
            "Adviesproduct-ID. Gebruik 'Synchroniseer nu' om ze op te halen.",
        )
    if source_product_id is None:
        return
    if not is_bottle:
        raise HTTPException(
            400,
            "Een adviesproduct-ID kan alleen aan een fles-SKU worden gekoppeld",
        )
    query = db.query(SKU).filter(
        SKU.organization_id == organization_id,
        SKU.source_product_id == source_product_id,
    )
    if exclude_sku_id is not None:
        query = query.filter(SKU.id != exclude_sku_id)
    if db.query(query.exists()).scalar():
        raise HTTPException(
            400,
            f"Adviesproduct-ID '{source_product_id}' is al aan een andere fles gekoppeld",
        )


def _validate_bottle_link(
    db: Session,
    *,
    organization_id: int | None,
    is_bottle: bool,
    bottle_sku_id: int | None,
    self_sku_id: int | None = None,
) -> None:
    """Check the box → bottle link against the SKU's *resulting* state.

    The link only means something in one direction: a box contains bottles.
    Allowing it on a bottle (or pointing it at another box) would produce a
    conversion nobody can interpret at pick time, so both are refused here
    rather than left to fail later in the warehouse.
    """
    if bottle_sku_id is None:
        return
    if is_bottle:
        raise HTTPException(
            400, "Alleen een doosproduct kan aan een fles gekoppeld worden"
        )
    if self_sku_id is not None and bottle_sku_id == self_sku_id:
        raise HTTPException(400, "Een product kan niet aan zichzelf gekoppeld worden")
    bottle = db.get(SKU, bottle_sku_id)
    if bottle is None or bottle.organization_id != organization_id:
        raise HTTPException(400, "Flesproduct niet gevonden bij deze handelaar")
    if not bottle.is_bottle:
        raise HTTPException(400, f"'{bottle.name}' is geen flesproduct")


def _is_ean_unique_violation(exc: IntegrityError) -> bool:
    """Whether an IntegrityError is the per-org EAN uniqueness violation.

    The app-level _ean_conflict check catches sequential duplicates; this only
    fires on a genuine race where two requests pass that check before either
    commits. Postgres names the constraint in the message; SQLite lists the
    columns — match either so the race surfaces as a clean 400, not a 500.
    """
    msg = str(getattr(exc, "orig", exc))
    return "uq_skus_org_ean" in msg or "skus.ean" in msg


def _is_source_product_unique_violation(exc: IntegrityError) -> bool:
    msg = str(getattr(exc, "orig", exc))
    return (
        "uq_skus_org_source_product_id" in msg
        or "skus.organization_id, skus.source_product_id" in msg
    )


def _sku_to_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        category=sku.category,
        attributes=sku.attributes_dict,
        supplier_id=sku.supplier_id,
        supplier_name=sku.supplier.name if sku.supplier else None,
        is_bottle=sku.is_bottle,
        bottle_sku_id=sku.bottle_sku_id,
        bottle_sku_code=sku.bottle_sku.sku_code if sku.bottle_sku else None,
        bottle_sku_name=sku.bottle_sku.name if sku.bottle_sku else None,
        source_product_id=sku.source_product_id,
        product_type=sku.product_type,
        ean=sku.ean,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )


@router.get("", response_model=list[SKUResponse])
def list_skus(
    active_only: bool = False,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(SKU).options(
        selectinload(SKU.reference_images),
        selectinload(SKU.attributes),
        selectinload(SKU.supplier),
    )
    if active_only:
        query = query.filter(SKU.active.is_(True))
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(SKU.organization_id == user.organization_id)
        # Couriers (no org) are platform-level warehouse workers and need
        # visibility into all SKUs to link inbound shipment lines.
    if organization_id is not None and (
        user.is_platform_admin or user.role == "courier"
    ):
        query = query.filter(SKU.organization_id == organization_id)
    if search and search.strip():
        like = f"%{search.strip()}%"
        # Search server-side so wines beyond the current page are still found.
        # Match name, SKU code, EAN, category, the producent attribute, or the
        # supplier (leverancier) name. Vision (wine) products have a NULL ean,
        # so the EAN term is a no-op for them and needs no module gate.
        producent_subq = db.query(SKUAttribute.sku_id).filter(
            SKUAttribute.key == "producent",
            SKUAttribute.value.ilike(like),
        )
        supplier_subq = db.query(Supplier.id).filter(Supplier.name.ilike(like))
        query = query.filter(
            or_(
                SKU.name.ilike(like),
                SKU.sku_code.ilike(like),
                SKU.ean.ilike(like),
                SKU.category.ilike(like),
                SKU.id.in_(producent_subq),
                SKU.supplier_id.in_(supplier_subq),
            )
        )
    skus = query.order_by(SKU.name).offset(offset).limit(limit).all()
    return [_sku_to_response(s) for s in skus]


@router.get("/options", response_model=list[SKUOption])
def list_sku_options(
    active_only: bool = False,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lightweight SKU list (id, sku_code, name only) for dialog pickers.

    Returns a slim column projection without attributes or reference images,
    so dialogs that need the full catalog (manual order, customer assortment)
    don't pull large SKUResponse payloads. Returns all matching SKUs — the
    projection is cheap, so there is no truncating page limit.
    """
    # Pull the producent attribute (one row per SKU at most) as a subquery so it
    # joins cheaply without inflating rows.
    producent_subq = (
        db.query(SKUAttribute.sku_id, SKUAttribute.value.label("producent"))
        .filter(SKUAttribute.key == "producent")
        .subquery()
    )
    query = (
        db.query(
            SKU.id,
            SKU.sku_code,
            SKU.name,
            SKU.is_bottle,
            SKU.category,
            Supplier.name.label("supplier_name"),
            producent_subq.c.producent,
        )
        .outerjoin(Supplier, SKU.supplier_id == Supplier.id)
        .outerjoin(producent_subq, producent_subq.c.sku_id == SKU.id)
    )
    if active_only:
        query = query.filter(SKU.active.is_(True))
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(SKU.organization_id == user.organization_id)
        # Couriers (no org) are platform-level workers and see all SKUs.
    if organization_id is not None and (
        user.is_platform_admin or user.role == "courier"
    ):
        query = query.filter(SKU.organization_id == organization_id)
    rows = query.order_by(SKU.name).all()
    return [
        SKUOption(
            id=r.id,
            sku_code=r.sku_code,
            name=r.name,
            is_bottle=r.is_bottle,
            category=r.category,
            producent=r.producent,
            supplier_name=r.supplier_name,
        )
        for r in rows
    ]


def _assert_org_supports_product_type(
    db: Session, organization_id: int | None, product_type: str
) -> None:
    """Invariant: an org may only hold products whose pick method it has enabled.

    A vision SKU requires ``vision_picking``; a barcode SKU requires
    ``barcode_picking``. Global SKUs (no organization, e.g. created by a platform
    admin) are exempt — there is no org whose modules could be checked.
    """
    if organization_id is None:
        return
    required = PICKING_MODULE_BY_PRODUCT_TYPE.get(product_type)
    if required is None:
        return
    org = db.get(Organization, organization_id)
    if org and required not in org.modules:
        raise HTTPException(
            400,
            f"Deze organisatie heeft module '{required}' niet ingeschakeld; "
            f"kan geen {product_type}-product aanmaken",
        )


@router.post("/advice-sync", response_model=AdviceProductSyncResponse)
def sync_advice_products_now(
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Pull the advice app's product snapshot now instead of waiting for the loop.

    Deliberately a sync ``def``: applying a snapshot runs image analysis through
    ``asyncio.run``, which fails inside a running event loop. FastAPI puts a
    sync route on the threadpool, the same place the periodic loop reaches via
    ``asyncio.to_thread``.
    """
    if not settings.advice_products_feed_configured:
        raise HTTPException(503, "De adviesproductfeed is niet geconfigureerd")

    organization_id = settings.advice_stock_organization_id
    if not user.is_platform_admin and user.organization_id != organization_id:
        raise HTTPException(403, "Geen toegang tot de adviesproductfeed")

    try:
        with advice_sync_slot():
            summary = sync_advice_products(
                db,
                organization_id=organization_id,
                client=AdviceProductsClient(),
                # Reference images stay with the periodic loop: downloading and
                # analysing them takes tens of seconds, while the operator only
                # needs the SKU to exist to link and book a shipment line. The
                # loop still picks them up, since it imports an image only for a
                # product that has none yet.
                import_images=False,
            )
    except AdviceProductSyncBusy:
        raise HTTPException(409, "Er loopt al een synchronisatie; probeer het zo opnieuw")
    except AdviceProductSyncError as exc:
        raise HTTPException(502, str(exc))

    return AdviceProductSyncResponse(
        received=summary.received,
        created=summary.created,
        updated=summary.updated,
        deactivated=summary.deactivated,
        conflicts=summary.conflicts,
    )


@router.post("", response_model=SKUResponse, status_code=201)
def create_sku(
    data: SKUCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    source_product_id = _normalize_source_product_id(data.source_product_id)
    _validate_source_product_link(
        db,
        organization_id=user.organization_id,
        source_product_id=source_product_id,
        is_bottle=data.is_bottle,
    )
    _validate_bottle_link(
        db,
        organization_id=user.organization_id,
        is_bottle=data.is_bottle,
        bottle_sku_id=data.bottle_sku_id,
    )

    # For wine: auto-generate sku_code and name from attributes
    if data.category == "wine":
        sku_code = data.sku_code or generate_wine_sku_code(data.attributes)
        name = data.name or generate_wine_display_name(data.attributes)
        description = " ".join(data.attributes.get(k, "") for k in WINE_ATTRIBUTE_KEYS)
    else:
        if not data.sku_code:
            raise HTTPException(400, "sku_code is verplicht voor niet-wijn producten")
        if not data.name:
            raise HTTPException(400, "name is verplicht voor niet-wijn producten")
        sku_code = data.sku_code
        name = data.name
        description = name

    existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"SKU code '{sku_code}' bestaat al")

    # EAN format is validated by the schema; uniqueness is per organization.
    if data.ean and _ean_conflict(db, user.organization_id, data.ean):
        raise HTTPException(400, f"EAN '{data.ean}' bestaat al bij deze handelaar")

    _assert_org_supports_product_type(db, user.organization_id, data.product_type)

    sku = SKU(
        sku_code=sku_code,
        name=name,
        description=description,
        active=data.active,
        category=data.category,
        organization_id=user.organization_id,
        supplier_id=data.supplier_id,
        is_bottle=data.is_bottle,
        bottle_sku_id=data.bottle_sku_id,
        source_product_id=source_product_id,
        source_active=data.active if source_product_id else None,
        product_type=data.product_type,
        ean=data.ean,
    )
    sku.set_attributes(data.attributes)
    db.add(sku)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _is_ean_unique_violation(exc):
            raise HTTPException(400, f"EAN '{data.ean}' bestaat al bij deze handelaar")
        if _is_source_product_unique_violation(exc):
            raise HTTPException(400, "Dit adviesproduct-ID is al gekoppeld")
        raise
    recompute_active(sku, db)
    # A newly-created product may be the missing catalog entry that a channel
    # order is parked on (pending_product) — flag a re-sync so it self-heals.
    if sku.ean:
        resync_channel_for_new_ean(db, sku.organization_id, sku.ean)
    db.commit()
    db.refresh(sku)
    publish_event(
        "sku_created",
        details={"sku_code": sku.sku_code, "name": sku.name},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )
    return _sku_to_response(sku)


@router.get("/{sku_id}", response_model=SKUResponse)
def get_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin and user.role != "courier":
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")
    return _sku_to_response(sku)


@router.patch("/{sku_id}", response_model=SKUResponse)
def update_sku(
    sku_id: int,
    data: SKUUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    changed_fields = data.model_dump(exclude_unset=True)

    resulting_is_bottle = (
        data.is_bottle if data.is_bottle is not None else sku.is_bottle
    )
    resulting_source_product_id = (
        _normalize_source_product_id(data.source_product_id)
        if "source_product_id" in changed_fields
        else sku.source_product_id
    )
    _validate_source_product_link(
        db,
        organization_id=sku.organization_id,
        source_product_id=resulting_source_product_id,
        is_bottle=resulting_is_bottle,
        exclude_sku_id=sku.id,
    )

    # Validate the *resulting* pair, so flipping a linked box into a bottle is
    # refused instead of leaving a link that points the wrong way.
    resulting_bottle_sku_id = (
        data.bottle_sku_id
        if "bottle_sku_id" in changed_fields
        else sku.bottle_sku_id
    )
    _validate_bottle_link(
        db,
        organization_id=sku.organization_id,
        is_bottle=resulting_is_bottle,
        bottle_sku_id=resulting_bottle_sku_id,
        self_sku_id=sku.id,
    )
    if "bottle_sku_id" in changed_fields:
        sku.bottle_sku_id = resulting_bottle_sku_id

    # Explicit active wins; otherwise it is recomputed below for opted-in orgs.
    active_set_explicitly = data.active is not None
    if active_set_explicitly:
        sku.active = data.active

    if "supplier_id" in changed_fields:
        sku.supplier_id = data.supplier_id

    if "is_bottle" in changed_fields and data.is_bottle is not None:
        sku.is_bottle = data.is_bottle

    if "source_product_id" in changed_fields:
        sku.source_product_id = resulting_source_product_id
        # Linking keeps the merchant's current availability until the first
        # snapshot overwrites it; unlinking hands control back to them.
        sku.source_active = sku.active if resulting_source_product_id else None

    product_type_changed = "product_type" in changed_fields and data.product_type is not None
    if product_type_changed:
        _assert_org_supports_product_type(db, sku.organization_id, data.product_type)
        sku.product_type = data.product_type

    if "name" in changed_fields and data.name is not None:
        sku.name = data.name

    if "sku_code" in changed_fields and data.sku_code:
        conflict = db.query(SKU).filter(
            SKU.sku_code == data.sku_code, SKU.id != sku_id
        ).first()
        if conflict:
            raise HTTPException(400, f"SKU code '{data.sku_code}' bestaat al")
        sku.sku_code = data.sku_code

    ean_changed = "ean" in changed_fields
    if ean_changed:
        sku.ean = normalize_ean(data.ean)
        # Channel product ids were resolved via the old EAN, so they no longer
        # point at the right listing. The next write-back re-resolves them.
        sku.shopify_inventory_item_id = None
        sku.bol_offer_id = None

    # Whenever identity (type or EAN) changes, validate the resulting pair — not
    # just the field that was sent — so a partial update can never leave a
    # barcode product without a valid EAN, or a vision product carrying one.
    if product_type_changed or ean_changed:
        if sku.product_type == "barcode":
            if not sku.ean:
                raise HTTPException(400, "EAN is verplicht voor barcode-producten")
            if not is_valid_ean13(sku.ean):
                raise HTTPException(
                    400,
                    "Ongeldige EAN-13 (controleer de cijfers en het controlecijfer)",
                )
            if _ean_conflict(db, sku.organization_id, sku.ean, exclude_sku_id=sku_id):
                raise HTTPException(400, f"EAN '{sku.ean}' bestaat al bij deze handelaar")
        else:  # vision must never carry an EAN
            sku.ean = None

    if data.attributes is not None:
        sku.set_attributes(data.attributes)
        # Regenerate sku_code and name for wine SKUs when attributes change.
        # A merchant finishing an inbound concept (a wine missing its fields)
        # fills these in here; that is what flips it active without a photo.
        if sku.category == "wine":
            attrs = sku.attributes_dict
            if all(attrs.get(k) for k in WINE_ATTRIBUTE_KEYS):
                sku.name = generate_wine_display_name(attrs)
                # Inbound concepts keep their supplier code as identity, so a
                # later re-scan of the same code still finds this SKU instead of
                # creating a duplicate. Other wines get the canonical wine code.
                if (
                    attrs.get("source") != "inbound_scan"
                    and not sku.source_product_id
                ):
                    new_code = generate_wine_sku_code(attrs)
                    conflict = db.query(SKU).filter(SKU.sku_code == new_code, SKU.id != sku_id).first()
                    if conflict:
                        raise HTTPException(400, f"SKU code '{new_code}' bestaat al")
                    sku.sku_code = new_code

    # Re-evaluate the derived active flag now that fields may have changed, so a
    # finished concept goes active without needing a photo. Once it is complete
    # it is no longer a concept.
    if not active_set_explicitly:
        recompute_active(sku, db)
    if is_complete(sku) and sku.attributes_dict.get("status") == "concept":
        sku.set_attribute("status", "done")

    # A changed/added EAN may match a channel order parked in pending_product —
    # flag a re-sync so that order self-heals to active on the next pull.
    if ean_changed and sku.ean:
        resync_channel_for_new_ean(db, sku.organization_id, sku.ean)

    attempted_ean = sku.ean  # read before commit; sku expires on rollback
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _is_ean_unique_violation(exc):
            raise HTTPException(400, f"EAN '{attempted_ean}' bestaat al bij deze handelaar")
        if _is_source_product_unique_violation(exc):
            raise HTTPException(400, "Dit adviesproduct-ID is al gekoppeld")
        raise
    db.refresh(sku)
    publish_event(
        "sku_updated",
        details={"sku_code": sku.sku_code, "changed_fields": list(changed_fields.keys())},
        user=user,
        resource_type="sku",
        resource_id=sku.id,
    )
    return _sku_to_response(sku)


@router.delete("/{sku_id}", status_code=204)
def delete_sku(
    sku_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin:
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")

    if force:
        db.query(Booking).filter(Booking.sku_id == sku_id).delete()
        db.query(OrderLine).filter(OrderLine.sku_id == sku_id).delete()
        db.query(InboundShipmentLine).filter(InboundShipmentLine.sku_id == sku_id).delete()
        db.query(StockMovement).filter(StockMovement.sku_id == sku_id).delete()
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku_id).delete()
    else:
        blockers: list[str] = []
        if db.query(OrderLine).filter(OrderLine.sku_id == sku_id).first():
            blockers.append("order lines")
        if db.query(Booking).filter(Booking.sku_id == sku_id).first():
            blockers.append("bookings")
        if db.query(InboundShipmentLine).filter(InboundShipmentLine.sku_id == sku_id).first():
            blockers.append("inbound shipment lines")
        if db.query(StockMovement).filter(StockMovement.sku_id == sku_id).first():
            blockers.append("stock movements")
        if db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku_id).first():
            blockers.append("inventory balance")
        if blockers:
            raise HTTPException(
                409,
                f"Cannot delete SKU '{sku.sku_code}': still referenced by {', '.join(blockers)}",
            )

    # A box pointing at this bottle must not keep a dangling link. The FK does
    # this too, but doing it here keeps the behaviour identical on every engine
    # and makes the intent explicit: the box survives, unlinked.
    db.query(SKU).filter(SKU.bottle_sku_id == sku_id).update(
        {SKU.bottle_sku_id: None}, synchronize_session=False
    )

    sku_code = sku.sku_code
    db.delete(sku)
    db.commit()
    publish_event(
        "sku_deleted",
        details={"sku_code": sku_code},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )


@router.post("/{sku_id}/images", response_model=ReferenceImageResponse, status_code=201)
@observe()
async def upload_reference_image(
    sku_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    skip_wine_check: bool = Form(False),
    skip_duplicate_check: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_can_edit_photos),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")

    # Normalize uploads (incl. iPhone HEIC) to a real, upright JPEG so the
    # ``.jpg`` key is honest and downstream vision decoding always succeeds.
    # Pillow decode/encode is CPU-bound, so run it off the event loop to avoid
    # stalling concurrent requests on large phone photos.
    try:
        image_bytes = await asyncio.to_thread(normalize_upload_to_jpeg, image_bytes)
    except UnsupportedImageError:
        raise HTTPException(
            415,
            "Niet-ondersteund of beschadigd afbeeldingsbestand. "
            "Gebruik een JPG, PNG of HEIC.",
        )
    # A valid HEIC can decode into a larger JPEG; enforce the cap on the result.
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")

    image_key = f"reference_images/{sku_id}/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)

    ref_image = ReferenceImage(
        sku_id=sku_id,
        image_path=image_key,
        processing_status="pending",
        wine_check_overridden=skip_wine_check,
    )
    db.add(ref_image)
    db.commit()
    db.refresh(ref_image)

    # The order-promotion gate is "every line's SKU has a reference image row"
    # (matches order approval), so a freshly-uploaded image can unblock an order
    # parked in pending_images. Promote now instead of leaving it stuck until an
    # unrelated recompute happens.
    if promote_pending_images_orders_for_sku(db, sku_id):
        db.commit()

    background_tasks.add_task(
        _process_reference_image,
        ref_image.id,
        skip_wine_check=skip_wine_check,
        skip_duplicate_check=skip_duplicate_check,
        bind=db.get_bind(),
    )

    publish_event(
        "reference_image_uploaded",
        details={"sku_code": sku.sku_code, "image_id": ref_image.id},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )

    return ref_image


@router.post("/{sku_id}/images/{image_id}/retry", response_model=ReferenceImageResponse)
async def retry_reference_image_processing(
    sku_id: int,
    image_id: int,
    background_tasks: BackgroundTasks,
    skip_wine_check: bool = Form(False),
    skip_duplicate_check: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_can_edit_photos),
):
    image = (
        db.query(ReferenceImage)
        .filter(ReferenceImage.id == image_id, ReferenceImage.sku_id == sku_id)
        .first()
    )
    if not image:
        raise HTTPException(404, "Reference image not found")
    if image.processing_status in ("pending", "processing") and not _is_stale(image):
        raise HTTPException(409, "Beeldanalyse loopt al")

    effective_skip_wine_check = skip_wine_check or image.wine_check_overridden
    image.processing_status = "pending"
    image.processing_started_at = None
    image.wine_check_overridden = effective_skip_wine_check
    _clear_processing_error(image)
    db.commit()
    db.refresh(image)

    background_tasks.add_task(
        _process_reference_image,
        image.id,
        skip_wine_check=effective_skip_wine_check,
        skip_duplicate_check=skip_duplicate_check,
        bind=db.get_bind(),
    )

    publish_event(
        "reference_image_retry_requested",
        details={
            "image_id": image.id,
            "skip_wine_check": effective_skip_wine_check,
            "skip_duplicate_check": skip_duplicate_check,
        },
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )

    return image


@router.get("/{sku_id}/images", response_model=list[ReferenceImageResponse])
def list_reference_images(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin and user.role != "courier":
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")
    return sku.reference_images


@router.get(
    "/{sku_id}/images/status",
    response_model=list[ReferenceImageStatusResponse],
)
def list_reference_image_statuses(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lightweight polling endpoint — returns just status fields.

    Used by the SKU dialog to detect transitions out of pending/processing
    without re-fetching full image rows every few seconds.
    """
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    if not user.is_platform_admin and user.role != "courier":
        if user.organization_id:
            if sku.organization_id != user.organization_id:
                raise HTTPException(404, "SKU not found")
        elif sku.organization_id is not None:
            raise HTTPException(404, "SKU not found")
    return [
        ReferenceImageStatusResponse(
            id=img.id,
            processing_status=img.processing_status,
            processing_error_code=img.processing_error_code,
        )
        for img in sku.reference_images
    ]


@router.delete("/{sku_id}/images/{image_id}", status_code=204)
def delete_reference_image(
    sku_id: int,
    image_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_edit_photos),
):
    image = (
        db.query(ReferenceImage)
        .filter(ReferenceImage.id == image_id, ReferenceImage.sku_id == sku_id)
        .first()
    )
    if not image:
        raise HTTPException(404, "Reference image not found")
    sku = db.get(SKU, sku_id)
    storage.delete(image.image_path)
    db.delete(image)
    db.flush()
    if sku is not None:
        recompute_active(sku, db)
    db.commit()
    publish_event(
        "reference_image_deleted",
        details={"sku_code": sku.sku_code if sku else None, "image_id": image_id},
        user=user,
        resource_type="sku",
        resource_id=sku_id,
    )
