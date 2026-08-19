"""Two questions about the scan flow that the traces could not answer.

How often does the visual pass overrule the vector search, and how often was a
refusal wrong? Both are recorded as scores on the scan's own trace, so a
refusal keeps its verdict next to the photo that caused it.
"""

import pytest

from app.services import scan_metrics


@pytest.fixture
def captured_scores(monkeypatch):
    """Collect the scores that would have gone to Langfuse."""
    written: list[dict] = []

    def _fake_score(trace_id, name, value, *, data_type, comment=""):
        written.append(
            {"trace_id": trace_id, "name": name, "value": value, "comment": comment}
        )

    monkeypatch.setattr(scan_metrics, "_score", _fake_score)
    scan_metrics.reset_pending()
    yield written
    scan_metrics.reset_pending()


def _record(reason_code="needs_confirmation", **overrides):
    kwargs = {
        "trace_id": "trace-1",
        "session_id": "521",
        "reason_code": reason_code,
        "rejected": False,
        "rerank_ran": True,
        "rerank_agreed_with_vector": True,
        "candidate_sku_ids": [141, 142],
    }
    kwargs.update(overrides)
    scan_metrics.record_scan(**kwargs)


def test_every_scan_is_scored_with_its_own_reason(captured_scores):
    _record(reason_code="sku_full", rejected=True)

    outcome = next(s for s in captured_scores if s["name"] == "scan_outcome")
    assert outcome["value"] == "sku_full"
    assert outcome["comment"] == "rejected"


def test_an_overruling_visual_pass_is_counted(captured_scores):
    """The step that can turn a correct vector hit into a refusal."""
    _record(rerank_agreed_with_vector=False)

    override = next(s for s in captured_scores if s["name"] == "rerank_override")
    assert override["value"] == 1
    _record(rerank_agreed_with_vector=True)
    assert [s["value"] for s in captured_scores if s["name"] == "rerank_override"] == [1, 0]


def test_a_skipped_visual_pass_is_not_counted_either_way(captured_scores):
    _record(rerank_ran=False, rerank_agreed_with_vector=None)

    assert not [s for s in captured_scores if s["name"] == "rerank_override"]


def test_booking_a_refused_candidate_marks_that_refusal(captured_scores):
    """The picker rescanned and booked one of the candidates: refusal was wrong."""
    _record(reason_code="not_ordered", rejected=True, candidate_sku_ids=[141, 142])

    scan_metrics.record_booking(session_id="521", sku_id=142)

    recovered = [s for s in captured_scores if s["name"] == "recovered_after_rejection"]
    assert len(recovered) == 1
    assert recovered[0]["trace_id"] == "trace-1"
    assert "142" in recovered[0]["comment"]


def test_a_refusal_is_only_marked_once(captured_scores):
    _record(reason_code="not_ordered", rejected=True)

    scan_metrics.record_booking(session_id="521", sku_id=142)
    scan_metrics.record_booking(session_id="521", sku_id=142)

    assert len([s for s in captured_scores if s["name"] == "recovered_after_rejection"]) == 1


def test_an_unrelated_booking_leaves_the_refusal_alone(captured_scores):
    """A different wine, or a different picking session, proves nothing."""
    _record(reason_code="not_ordered", rejected=True, candidate_sku_ids=[141, 142])

    scan_metrics.record_booking(session_id="521", sku_id=999)
    scan_metrics.record_booking(session_id="777", sku_id=142)

    assert not [s for s in captured_scores if s["name"] == "recovered_after_rejection"]


def test_a_refusal_expires_out_of_the_window(captured_scores, monkeypatch):
    """Hours later, a booking says nothing about this morning's refusal."""
    _record(reason_code="not_ordered", rejected=True)

    real_monotonic = scan_metrics.time.monotonic
    monkeypatch.setattr(
        scan_metrics.time,
        "monotonic",
        lambda: real_monotonic() + scan_metrics.RECOVERY_WINDOW_SECONDS + 1,
    )
    scan_metrics.record_booking(session_id="521", sku_id=142)

    assert not [s for s in captured_scores if s["name"] == "recovered_after_rejection"]


def test_a_booked_scan_is_never_parked_for_recovery(captured_scores):
    _record(reason_code="needs_confirmation", rejected=False)

    scan_metrics.record_booking(session_id="521", sku_id=142)

    assert not [s for s in captured_scores if s["name"] == "recovered_after_rejection"]
