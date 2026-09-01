"""Quality metrics for the scan flow, recorded as Langfuse scores.

Two questions about the scan flow could not be answered from the traces:

* **How often does the visual rerank overrule the vector search?** That
  override is the step that can turn a correct vector hit into a refusal, so
  its frequency is the number to watch when the rerank prompt or the model
  behind it changes.
* **How often was a refusal wrong?** A refused scan followed, minutes later
  in the same picking session, by a booking of a SKU that was on the table
  during that refusal is the picker telling us the box was bookable after all.
  That pairing is free ground truth — no labelling round needed.

Both are written as scores on the scan's own trace, so a refusal keeps its
verdict next to the photo that caused it.

The pending-rejection store is per process and in memory: it holds a short
window of refusals and is consulted when a booking is confirmed. Losing it on
restart, or missing a pairing because the two requests landed on different
workers, costs a data point on a trend line and nothing else — it is never
consulted for anything the picker sees.
"""

import logging
import time

from collections import deque
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

# How long after a refusal a booking still counts as "the picker recovered
# from it". Long enough for the picker to rescan and confirm, short enough
# that the next box on the pallet does not get credited to it.
RECOVERY_WINDOW_SECONDS = 600

# Bounded so a long picking day cannot grow this without limit.
_MAX_PENDING = 200


@dataclass(frozen=True)
class _PendingRejection:
    """A refused scan, waiting to see whether the picker books one of its candidates."""

    trace_id: str
    session_id: str
    reason_code: str
    candidate_sku_ids: frozenset[int]
    created_at: float = field(default_factory=time.monotonic)


_pending: deque[_PendingRejection] = deque(maxlen=_MAX_PENDING)


def _client():
    """Return the Langfuse client, or None when tracing is not configured."""
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        logger.debug("Langfuse client unavailable for scan scores", exc_info=True)
        return None


def _score(trace_id: str, name: str, value, *, data_type: str, comment: str = "") -> None:
    client = _client()
    if client is None or not trace_id:
        return
    try:
        client.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            data_type=data_type,
            comment=comment or None,
        )
    except Exception:
        # Metrics must never break a scan.
        logger.debug("Failed to write scan score %s", name, exc_info=True)


def record_scan(
    *,
    trace_id: str,
    session_id: str,
    reason_code: str,
    rejected: bool,
    rerank_ran: bool,
    rerank_agreed_with_vector: bool | None,
    candidate_sku_ids: list[int],
) -> None:
    """Score one finished scan and, when refused, park it for the recovery check."""
    _score(
        trace_id,
        "scan_outcome",
        reason_code,
        data_type="CATEGORICAL",
        comment="rejected" if rejected else "booked",
    )

    if rerank_ran and rerank_agreed_with_vector is not None:
        _score(
            trace_id,
            "rerank_override",
            0 if rerank_agreed_with_vector else 1,
            data_type="NUMERIC",
            comment=(
                "visual check confirmed the vector ranking"
                if rerank_agreed_with_vector
                else "visual check picked a different SKU than the vector ranking"
            ),
        )

    if rejected and trace_id and candidate_sku_ids:
        _pending.append(
            _PendingRejection(
                trace_id=trace_id,
                session_id=session_id,
                reason_code=reason_code,
                candidate_sku_ids=frozenset(candidate_sku_ids),
            )
        )


def record_booking(*, session_id: str, sku_id: int) -> None:
    """Mark refusals in this session that this booking contradicts.

    A booking of a SKU that was among a refused scan's candidates means that
    box could be booked after all — the refusal cost the picker a scan.
    """
    if not _pending:
        return

    now = time.monotonic()
    survivors: list[_PendingRejection] = []
    recovered: list[_PendingRejection] = []

    for pending in _pending:
        if now - pending.created_at > RECOVERY_WINDOW_SECONDS:
            continue
        if pending.session_id == session_id and sku_id in pending.candidate_sku_ids:
            recovered.append(pending)
        else:
            survivors.append(pending)

    _pending.clear()
    _pending.extend(survivors)

    for pending in recovered:
        logger.info(
            "Scan refusal %s in session %s was followed by a booking of SKU %s",
            pending.reason_code, pending.session_id, sku_id,
        )
        _score(
            pending.trace_id,
            "recovered_after_rejection",
            1,
            data_type="NUMERIC",
            comment=f"SKU {sku_id} booked within the window after '{pending.reason_code}'",
        )


def reset_pending() -> None:
    """Drop the pending-rejection window (tests)."""
    _pending.clear()
