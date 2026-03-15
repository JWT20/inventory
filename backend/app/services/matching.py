import logging
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SKU

logger = logging.getLogger(__name__)


def find_best_matches(
    db: Session,
    embedding: list[float],
    top_n: int = 5,
    sku_ids: list[int] | None = None,
) -> list[tuple[SKU, float]]:
    """Return the top-N matching SKUs for a given embedding.

    Returns a list of (sku, similarity) tuples, ordered by similarity descending.
    Only includes active SKUs.  When *sku_ids* is provided, only matches against
    reference images belonging to those SKUs (order-aware scanning).
    """
    t0 = time.perf_counter()
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    params: dict = {"embedding": embedding_str, "top_n": top_n}

    sku_filter = ""
    if sku_ids:
        sku_filter = "AND ri.sku_id = ANY(:sku_ids)"
        params["sku_ids"] = sku_ids

    rows = db.execute(
        text(f"""
            SELECT ri.sku_id, 1 - (ri.embedding <=> :embedding) AS similarity
            FROM reference_images ri
            JOIN skus s ON s.id = ri.sku_id
            WHERE s.active = true
              AND ri.embedding IS NOT NULL
              {sku_filter}
            ORDER BY ri.embedding <=> :embedding
            LIMIT :top_n
        """),
        params,
    ).fetchall()
    vector_ms = (time.perf_counter() - t0) * 1000

    if not rows:
        logger.info("[TIMING] pgvector_search=%.0fms (no results)", vector_ms)
        return []

    t1 = time.perf_counter()
    sku_ids = [row[0] for row in rows]
    skus_by_id = {s.id: s for s in db.query(SKU).filter(SKU.id.in_(sku_ids)).all()}
    sku_load_ms = (time.perf_counter() - t1) * 1000

    results = []
    for sku_id, similarity in rows:
        sku = skus_by_id.get(sku_id)
        if sku:
            results.append((sku, float(similarity)))

    logger.info("[TIMING] pgvector_search=%.0fms sku_load=%.0fms (%d results)", vector_ms, sku_load_ms, len(results))
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
