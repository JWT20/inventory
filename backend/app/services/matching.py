import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ReferenceImage, SKU

logger = logging.getLogger(__name__)


def find_best_match(
    db: Session, embedding: list[float]
) -> tuple[SKU | None, float]:
    """Find the best matching SKU for a given embedding using cosine similarity.

    Returns (matched_sku, confidence) or (None, 0.0) if no match found.
    """
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    # Use pgvector cosine distance operator (<=>), which returns distance (0 = identical).
    # Similarity = 1 - distance.
    result = db.execute(
        text("""
            SELECT ri.sku_id, 1 - (ri.embedding <=> :embedding) AS similarity
            FROM reference_images ri
            JOIN skus s ON s.id = ri.sku_id
            WHERE s.active = true
            ORDER BY ri.embedding <=> :embedding
            LIMIT 1
        """),
        {"embedding": embedding_str},
    ).first()

    if result is None:
        return None, 0.0

    sku_id, similarity = result
    similarity = float(similarity)

    if similarity < settings.match_threshold:
        logger.info(
            "Best match sku_id=%d has similarity %.3f, below threshold %.3f",
            sku_id,
            similarity,
            settings.match_threshold,
        )
        return None, similarity

    sku = db.get(SKU, sku_id)
    logger.info("Matched SKU %s with confidence %.3f", sku.sku_code, similarity)
    return sku, similarity
