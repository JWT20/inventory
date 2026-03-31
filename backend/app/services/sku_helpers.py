"""Shared SKU helpers used by multiple routers (skus, receiving)."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SKU
from app.schemas import SKUResponse


def check_duplicate_embedding(
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
    if row and row[1] >= settings.duplicate_similarity_threshold:
        sku = db.get(SKU, row[0])
        return sku, float(row[1])
    return None, 0.0


def sku_to_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        sku_code=sku.sku_code,
        name=sku.name,
        description=sku.description,
        active=sku.active,
        producent=sku.producent,
        wijnaam=sku.wijnaam,
        wijntype=sku.wijntype,
        jaargang=sku.jaargang,
        volume=sku.volume,
        created_at=sku.created_at,
        updated_at=sku.updated_at,
        image_count=len(sku.reference_images),
    )
