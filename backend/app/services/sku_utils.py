import re

from app.models import SKU
from app.schemas import SKUResponse


def generate_sku_code(producer: str, wine_name: str, wine_type: str, vintage: int | None, volume: str) -> str:
    """Generate a deterministic SKU code from wine attributes."""
    def slugify(s: str) -> str:
        s = s.upper().strip()
        s = re.sub(r"[^A-Z0-9]+", "-", s)
        return s.strip("-")

    vol = re.sub(r"[^0-9]", "", volume)
    parts = [slugify(producer), slugify(wine_name), slugify(wine_type)]
    if vintage:
        parts.append(str(vintage))
    parts.append(vol)
    return "-".join(p for p in parts if p)


def generate_display_name(producer: str, wine_name: str, vintage: int | None, volume: str) -> str:
    """Generate a human-readable display name."""
    parts = [producer, wine_name]
    if vintage:
        parts.append(str(vintage))
    parts.append(volume)
    return " ".join(parts)


def sku_to_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        producer=sku.producer,
        wine_name=sku.wine_name,
        wine_type=sku.wine_type,
        vintage=sku.vintage,
        volume=sku.volume,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )
