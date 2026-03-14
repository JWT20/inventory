import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SKU

logger = logging.getLogger(__name__)


def find_best_matches(
    db: Session, embedding: list[float], top_n: int = 5
) -> list[tuple[SKU, float]]:
    """Return the top-N matching SKUs for a given embedding.

    Returns a list of (sku, similarity) tuples, ordered by similarity descending.
    Only includes active SKUs.
    """
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    rows = db.execute(
        text("""
            SELECT ri.sku_id, 1 - (ri.embedding <=> :embedding) AS similarity
            FROM reference_images ri
            JOIN skus s ON s.id = ri.sku_id
            WHERE s.active = true
              AND ri.embedding IS NOT NULL
            ORDER BY ri.embedding <=> :embedding
            LIMIT :top_n
        """),
        {"embedding": embedding_str, "top_n": top_n},
    ).fetchall()

    if not rows:
        return []

    sku_ids = [row[0] for row in rows]
    skus_by_id = {s.id: s for s in db.query(SKU).filter(SKU.id.in_(sku_ids)).all()}

    results = []
    for sku_id, similarity in rows:
        sku = skus_by_id.get(sku_id)
        if sku:
            results.append((sku, float(similarity)))
    return results


def find_best_match(
    db: Session, embedding: list[float]
) -> tuple[SKU | None, float]:
    """Find the best matching SKU for a given embedding using cosine similarity.

    Returns (matched_sku, confidence) or (None, 0.0) if no match found.
    """
    candidates = find_best_matches(db, embedding, top_n=1)

    if not candidates:
        return None, 0.0

    sku, similarity = candidates[0]

    if similarity < settings.match_threshold:
        logger.info(
            "Best match sku_id=%d has similarity %.3f, below threshold %.3f",
            sku.id,
            similarity,
            settings.match_threshold,
        )
        return None, similarity

    logger.info("Matched SKU %s with confidence %.3f", sku.sku_code, similarity)
    return sku, similarity
