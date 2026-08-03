"""Pull bottle products from the advice app into Dockscan.

The advice app owns product-family identity and commercial bottle metadata.
Dockscan owns SKU codes, stock and scan reference images. The sync is a full,
idempotent snapshot for one configured organization and never touches box SKUs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Organization, ReferenceImage, SKU
from app.schemas import generate_wine_display_name, generate_wine_sku_code
from app.services.embedding import UnsupportedImageError, normalize_upload_to_jpeg
from app.services.product_status import recompute_active
from app.services.storage import storage


logger = logging.getLogger(__name__)

PRODUCTS_PATH = "/api/integrations/inventory/products"
MAX_PRODUCTS = 5_000
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class AdviceProductSyncError(RuntimeError):
    """The remote snapshot could not be fetched or safely applied."""


class AdviceProduct(BaseModel):
    source_product_id: str = Field(..., min_length=1, max_length=100)
    producer: str | None = Field(default=None, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    vintage: str | None = Field(default=None, max_length=20)
    color: str = Field(..., min_length=1, max_length=50)
    active: bool
    image_url: str | None = Field(default=None, max_length=2_000)

    @field_validator("source_product_id", "name", "color")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("waarde mag niet leeg zijn")
        return value

    @field_validator("producer", "vintage", "image_url")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class AdviceProductSnapshot(BaseModel):
    products: list[AdviceProduct]


@dataclass
class AdviceProductSyncSummary:
    received: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    images_imported: int = 0
    conflicts: list[str] = field(default_factory=list)
    image_errors: list[str] = field(default_factory=list)


class AdviceProductsClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        vercel_bypass_secret: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or settings.advice_products_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.advice_products_api_key
        self.vercel_bypass_secret = (
            vercel_bypass_secret
            if vercel_bypass_secret is not None
            else settings.advice_products_vercel_bypass_secret
        )
        self._http = http_client

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _get(self, url: str, **kwargs) -> httpx.Response:
        if self._http is not None:
            return self._http.get(url, **kwargs)
        return httpx.get(url, timeout=30.0, follow_redirects=True, **kwargs)

    def fetch_snapshot(self) -> AdviceProductSnapshot:
        if not self.configured:
            raise AdviceProductSyncError("Adviesproductfeed is niet geconfigureerd")
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            }
            if self.vercel_bypass_secret:
                headers["x-vercel-protection-bypass"] = self.vercel_bypass_secret
            response = self._get(
                f"{self.base_url}{PRODUCTS_PATH}",
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdviceProductSyncError(
                "Adviesproductfeed is tijdelijk niet bereikbaar"
            ) from exc

        if len(response.content) > MAX_FEED_BYTES:
            raise AdviceProductSyncError("Adviesproductfeed is te groot")
        try:
            snapshot = AdviceProductSnapshot.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AdviceProductSyncError(
                "Adviesproductfeed bevat ongeldige JSON of productgegevens"
            ) from exc
        if not snapshot.products:
            raise AdviceProductSyncError(
                "Adviesproductfeed is leeg; bestaande producten zijn niet gewijzigd"
            )
        if len(snapshot.products) > MAX_PRODUCTS:
            raise AdviceProductSyncError("Adviesproductfeed bevat te veel producten")

        ids = [product.source_product_id for product in snapshot.products]
        if len(ids) != len(set(ids)):
            raise AdviceProductSyncError(
                "Adviesproductfeed bevat dubbele source_product_id-waarden"
            )
        return snapshot

    def download_image(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AdviceProductSyncError("Adviesproductfoto moet een HTTPS-URL hebben")
        headers = {"Accept": "image/*"}
        feed_origin = urlparse(self.base_url)
        if (parsed.scheme, parsed.netloc) == (feed_origin.scheme, feed_origin.netloc):
            # Only send the feed credential back to the configured advice-app
            # origin. External signed storage URLs must never receive it.
            headers["Authorization"] = f"Bearer {self.api_key}"
            if self.vercel_bypass_secret:
                headers["x-vercel-protection-bypass"] = self.vercel_bypass_secret
        try:
            response = self._get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdviceProductSyncError(
                "Adviesproductfoto kon niet worden gedownload"
            ) from exc
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            raise AdviceProductSyncError("Adviesproductfoto is geen afbeelding")
        if len(response.content) > MAX_IMAGE_BYTES:
            raise AdviceProductSyncError("Adviesproductfoto is te groot")
        return response.content


_COLOR_TO_WINE_TYPE = {
    "red": "Rood",
    "white": "Wit",
    "rose": "Rosé",
    "rosé": "Rosé",
    "sparkling": "Mousserend",
}


def _wine_attributes(product: AdviceProduct) -> dict[str, str]:
    wine_type = _COLOR_TO_WINE_TYPE.get(
        product.color.casefold(), product.color.strip().capitalize()
    )
    return {
        "producent": product.producer or "",
        "wijnaam": product.name,
        "wijntype": wine_type,
        "volume": "750",
    }


def _new_bottle_sku_code(
    product: AdviceProduct,
    *,
    disambiguate: bool = False,
) -> str:
    # Unit suffix is Dockscan's own human-readable convention. Identity and
    # idempotency never depend on it; source_product_id does that work.
    base_code = generate_wine_sku_code(_wine_attributes(product))
    if not disambiguate:
        return f"{base_code}-FLES"
    suffix = (
        hashlib.sha256(product.source_product_id.encode()).hexdigest()[:8].upper()
    )
    return f"{base_code}-{suffix}-FLES"


def _update_sku_from_product(
    sku: SKU,
    product: AdviceProduct,
    db: Session,
) -> None:
    attrs = _wine_attributes(product)
    sku.name = generate_wine_display_name(attrs)
    sku.description = " ".join(
        part for part in [product.producer, product.name, product.vintage] if part
    )
    sku.set_attributes(attrs)
    sku.source_active = product.active
    sku.active = product.active
    # Organizations that auto-inactivate lower this again when the bottle has
    # no usable reference image; for everyone else the feed value stands.
    recompute_active(sku, db)


def _copy_initial_reference_image(
    db: Session,
    sku: SKU,
    image_url: str,
    client: AdviceProductsClient,
) -> int:
    raw = client.download_image(image_url)
    try:
        image_bytes = normalize_upload_to_jpeg(raw)
    except UnsupportedImageError as exc:
        raise AdviceProductSyncError(
            "Adviesproductfoto heeft een niet-ondersteund formaat"
        ) from exc
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise AdviceProductSyncError("Genormaliseerde adviesproductfoto is te groot")

    image_key = f"reference_images/{sku.id}/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)
    image = ReferenceImage(
        sku_id=sku.id,
        image_path=image_key,
        processing_status="pending",
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return image.id


def sync_advice_products(
    db: Session,
    *,
    organization_id: int,
    client: AdviceProductsClient,
    import_images: bool = True,
) -> AdviceProductSyncSummary:
    """Fetch and apply one complete product snapshot.

    Product changes commit atomically before optional reference-image downloads.
    Image failures are isolated: they never roll back product identity or cause
    existing products to disappear.
    """
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise AdviceProductSyncError(
            "Adviesproductfeed-organisatie is niet correct geconfigureerd"
        )
    if "vision_picking" not in organization.modules:
        raise AdviceProductSyncError(
            "Adviesproductfeed-organisatie heeft vision_picking niet ingeschakeld"
        )

    snapshot = client.fetch_snapshot()
    summary = AdviceProductSyncSummary(received=len(snapshot.products))
    existing = (
        db.query(SKU)
        .filter(
            SKU.organization_id == organization_id,
            SKU.source_product_id.is_not(None),
        )
        .all()
    )
    existing_by_source_id = {sku.source_product_id: sku for sku in existing}
    received_ids = {product.source_product_id for product in snapshot.products}
    images_to_import: list[tuple[int, str, str]] = []

    try:
        for product in snapshot.products:
            sku = existing_by_source_id.get(product.source_product_id)
            if sku is not None:
                if not sku.is_bottle:
                    raise AdviceProductSyncError(
                        f"Adviesproduct {product.source_product_id} is aan een doos-SKU gekoppeld"
                    )
                _update_sku_from_product(sku, product, db)
                summary.updated += 1
            else:
                sku_code = _new_bottle_sku_code(product)
                if db.query(SKU).filter(SKU.sku_code == sku_code).first() is not None:
                    sku_code = _new_bottle_sku_code(product, disambiguate=True)
                    if (
                        db.query(SKU).filter(SKU.sku_code == sku_code).first()
                        is not None
                    ):
                        summary.conflicts.append(
                            f"{product.source_product_id}: SKU-code {sku_code} bestaat al"
                        )
                        continue
                attrs = _wine_attributes(product)
                sku = SKU(
                    sku_code=sku_code,
                    name=generate_wine_display_name(attrs),
                    description=" ".join(
                        part
                        for part in [product.producer, product.name, product.vintage]
                        if part
                    ),
                    active=product.active,
                    category="wine",
                    organization_id=organization_id,
                    is_bottle=True,
                    source_product_id=product.source_product_id,
                    source_active=product.active,
                    product_type="vision",
                )
                sku.set_attributes(attrs)
                db.add(sku)
                db.flush()
                recompute_active(sku, db)
                existing_by_source_id[product.source_product_id] = sku
                summary.created += 1

            if product.image_url and not sku.reference_images:
                images_to_import.append(
                    (sku.id, product.source_product_id, product.image_url)
                )

        for sku in existing:
            if sku.source_product_id not in received_ids and sku.active:
                sku.active = False
                summary.deactivated += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    if not import_images:
        return summary

    for sku_id, source_product_id, image_url in images_to_import:
        sku = db.get(SKU, sku_id)
        if sku is None:
            continue
        if db.query(ReferenceImage).filter(ReferenceImage.sku_id == sku_id).first():
            continue
        try:
            image_id = _copy_initial_reference_image(db, sku, image_url, client)
            summary.images_imported += 1
            # Reuse Dockscan's established image-analysis pipeline. The sync
            # runs in a worker thread, so a dedicated event loop is safe here.
            from app.routers.skus import _process_reference_image

            asyncio.run(
                _process_reference_image(
                    image_id,
                    skip_wine_check=False,
                    skip_duplicate_check=False,
                    bind=db.get_bind(),
                )
            )
        except Exception as exc:
            db.rollback()
            logger.exception(
                "Advice product image import failed for %s", source_product_id
            )
            summary.image_errors.append(f"{source_product_id}: {type(exc).__name__}")

    return summary


def sync_advice_products_once() -> AdviceProductSyncSummary | None:
    """Run one configured sync with its own session; failures never escape."""
    if not settings.advice_products_sync_enabled:
        return None
    db = SessionLocal()
    try:
        summary = sync_advice_products(
            db,
            organization_id=settings.advice_stock_organization_id,
            client=AdviceProductsClient(),
        )
        logger.info(
            "Advice product sync: received=%s created=%s updated=%s "
            "deactivated=%s images=%s conflicts=%s image_errors=%s",
            summary.received,
            summary.created,
            summary.updated,
            summary.deactivated,
            summary.images_imported,
            len(summary.conflicts),
            len(summary.image_errors),
        )
        for conflict in summary.conflicts:
            logger.error("Advice product sync conflict: %s", conflict)
        return summary
    except Exception:
        db.rollback()
        logger.exception("Advice product sync failed; existing products unchanged")
        return None
    finally:
        db.close()


async def advice_product_sync_loop(interval_seconds: int) -> None:
    """Pull immediately on startup and then at the configured interval."""
    logger.info("Advice product sync started (interval=%ss)", interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(sync_advice_products_once)
            except Exception:
                logger.exception("Advice product sync iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Advice product sync stopped")
        raise
