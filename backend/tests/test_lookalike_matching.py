"""Lookalike packaging: the scan must lose to the catalogue, not to the order.

Three variants of one product line arrive; the order asks for exactly one of
them. Their boxes differ in a single printed word, so their vision descriptions
— and therefore their embeddings — are near-identical.

The old flow searched only the SKUs open in the order. With the other two
variants excluded from the pool, scanning the wrong box still cleared the match
threshold against the one SKU that happened to be open, and it was booked. These
tests pin the behaviour that replaced it: search the whole catalogue, let a
visual second pass decide between the lookalikes, and never auto-propose a box
the visual pass says is a different product.
"""

import asyncio
import io
from unittest.mock import patch

import pytest

from app.models import Customer, InventoryBalance, Order, OrderLine, ReferenceImage, SKU
from app.services.rerank import RerankVerdict
from app.services.storage import LocalStorage
from tests.conftest import auth_header
from tests.test_wine_filter import FAKE_IMAGE, _mock_process_package


def _tmp_storage(tmp_path):
    return LocalStorage(str(tmp_path))


def _make_sku(db, code, name):
    sku = SKU(sku_code=code, name=name)
    db.add(sku)
    db.flush()
    db.add(
        ReferenceImage(
            sku_id=sku.id,
            image_path=f"reference_images/{code}.jpg",
            embedding=[0.1] * 3072,
            processing_status="done",
        )
    )
    db.flush()
    return sku


def _make_open_order(db, org, sku, *, quantity=3):
    customer = Customer(name="Jansen", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(
        organization_id=org.id,
        reference="ORD-1",
        status="active",
        delivery_week="2026-W33",
    )
    db.add(order)
    db.flush()
    db.add(
        OrderLine(
            order_id=order.id,
            sku_id=sku.id,
            customer_id=customer.id,
            klant=customer.name,
            quantity=quantity,
            booked_count=0,
            delivery_day="wednesday",
        )
    )
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=org.id,
            quantity_on_hand=quantity,
            quantity_reserved=0,
        )
    )
    db.flush()
    return order


@pytest.fixture
def variants(db, sample_org):
    """Three lookalike variants; only the first is on the order."""
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    other = _make_sku(db, "CALY-ROSSO", "Calycanto Rosso")
    third = _make_sku(db, "CALY-ROSATO", "Calycanto Rosato")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()
    return ordered, other, third, order


def _post_book(client, courier_token, order):
    return client.post(
        "/api/receiving/book",
        files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
        data={"order_id": str(order.id), "scan_mode": "box"},
        headers=auth_header(courier_token),
    )


def test_confident_out_of_scope_wine_is_refused(
    client, courier_token, variants, tmp_path
):
    """The picker grabbed a different wine: refuse, never offer a one-tap book.

    This is the regression the whole change exists for. The scanned box is
    CALY-ROSSO, which is not on the order; previously it matched CALY-BIANCO —
    the only variant in the restricted pool — and was booked as such.

    The refusal stands whenever the visual pass and the vector ranking agree
    that the box is something else: here CALY-ROSSO leads by more than the
    ambiguity margin, so no open variant is close enough to be in doubt.
    """
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (other, 0.96, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.83, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=other.id,
            certainty="high",
            distinguishing_feature="label reads Rosso, not Bianco",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    # Never ordered here, so that is what the picker is told — not the old
    # catch-all about the SKU "not being open".
    assert detail["error"] == "not_ordered"
    assert "CALY-ROSSO" in detail["message"]
    assert "apart" in detail["message"]  # tells the picker what to do with it
    assert detail["distinguishing_feature"] == "label reads Rosso, not Bianco"


def test_near_tied_out_of_scope_pick_asks_the_picker_instead_of_refusing(
    client, courier_token, variants, tmp_path
):
    """Visual pass picks an out-of-scope variant that barely leads an open one.

    The pass answers "high" on every verdict it gives, so on its own it cannot
    carry a dead-end refusal. With an open variant inside the ambiguity margin
    of its pick — the box of Soave whose only difference from its sibling was
    the bottle count on one flap — the scan goes to the picker as an explicit
    comparison, with the variant the pass believed it saw shown alongside.
    """
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        # Neither the embedding nor the photos separate them.
        mock_match.return_value = [
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.93, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=other.id,
            certainty="high",
            distinguishing_feature="label reads Rosso, not Bianco",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku_code"] == "CALY-BIANCO"
    # A deliberate two-photo decision, never a clean hit.
    assert body["manual_review_required"] is True
    assert "CALY-ROSSO" in body["confirmation_reason"]
    assert "aantal en inhoud" in body["confirmation_reason"]

    # The variant the visual pass believed it saw is on screen, and not bookable.
    assert [a["sku_code"] for a in body["alternatives"]] == ["CALY-ROSSO"]
    assert body["alternatives"][0]["bookable"] is False


def test_unsure_between_variants_offers_a_choice_with_the_lookalike_shown(
    client, courier_token, variants, tmp_path
):
    """Unsure: propose the open variant, but show the out-of-scope one too.

    The lookalike is usually the box actually in the picker's hands, so it gets
    a card with its reference photo — and no confirm button, because it cannot
    be booked here.
    """
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.93, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=other.id,
            certainty="low",
            distinguishing_feature="",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    body = resp.json()
    # The proposal is the variant that is actually open.
    assert body["sku_code"] == "CALY-BIANCO"
    assert body["confirmation_token"]

    lookalikes = [a for a in body["alternatives"] if a["sku_code"] == "CALY-ROSSO"]
    assert len(lookalikes) == 1
    assert lookalikes[0]["bookable"] is False
    assert lookalikes[0]["confirmation_token"] == ""
    assert lookalikes[0]["note"]
    assert lookalikes[0]["reference_image_urls"]


def test_visual_rejection_offers_one_plausible_order_match_for_manual_review(
    client, courier_token, variants, tmp_path
):
    """A near-tied open SKU remains available as one explicit human decision."""
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (other, 0.823, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.814, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=None,
            certainty="high",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku_code"] == "CALY-BIANCO"
    assert body["manual_review_required"] is True
    assert body["alternatives"] == []
    assert "bevestig alleen" in body["confirmation_reason"]


def test_visual_rejection_stays_blocked_when_another_product_is_clearly_better(
    client, courier_token, variants, tmp_path
):
    """An order hit above 0.80 is unsafe when a catalogue rival leads by >0.05."""
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.82, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=None,
            certainty="high",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 404


def test_visual_rejection_stays_blocked_below_the_match_threshold(
    client, courier_token, variants, tmp_path
):
    """A near tie below 0.80 is not plausible enough to put in front of the picker."""
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (other, 0.79, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (ordered, 0.78, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=None,
            certainty="high",
            considered_sku_ids=[other.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 404


def test_visual_check_unavailable_still_asks_the_human(
    client, courier_token, variants, tmp_path
):
    """A Gemini outage degrades to "confirm this", not to a silent auto-match."""
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(ran=False, skip_reason="error: APIError")

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku_code"] == "CALY-BIANCO"
    assert "visual check unavailable" in body["confirmation_reason"]


def test_in_scope_rival_is_still_offered_as_a_bookable_alternative(
    client, courier_token, db, sample_org, variants, tmp_path
):
    """Two lookalikes both open on the order: both get a confirm button."""
    ordered, other, _third, order = variants
    # Put the second variant on the same order too.
    customer = db.query(Customer).first()
    db.add(
        OrderLine(
            order_id=order.id,
            sku_id=other.id,
            customer_id=customer.id,
            klant=customer.name,
            quantity=2,
            booked_count=0,
            delivery_day="wednesday",
        )
    )
    db.add(
        InventoryBalance(
            sku_id=other.id,
            organization_id=sample_org.id,
            quantity_on_hand=2,
            quantity_reserved=0,
        )
    )
    db.commit()

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(ran=False, skip_reason="disabled")

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    body = resp.json()
    rival = [a for a in body["alternatives"] if a["sku_code"] == "CALY-ROSSO"]
    assert len(rival) == 1
    assert rival[0]["bookable"] is True
    assert rival[0]["confirmation_token"]


def test_order_match_is_kept_when_catalogue_top_results_omit_it(
    client, courier_token, variants, tmp_path
):
    """The catalogue LIMIT may not hide the SKU that is open on the order."""
    ordered, other, third, order = variants

    def search_results(*_args, **kwargs):
        if kwargs.get("sku_ids"):
            return [
                (
                    ordered,
                    0.84,
                    "reference_images/CALY-BIANCO.jpg",
                    "Calycanto Bianco box",
                )
            ]
        # Simulate a global top-N filled by lookalikes. The ordered SKU is the
        # next catalogue result and therefore absent from this bounded list.
        return [
            (other, 0.95, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
            (third, 0.94, "reference_images/CALY-ROSATO.jpg", "Calycanto box"),
        ]

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches", side_effect=search_results), \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=ordered.id,
            certainty="high",
            distinguishing_feature="etiket zegt Bianco",
            considered_sku_ids=[other.id, third.id, ordered.id],
        )

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    assert resp.json()["sku_code"] == "CALY-BIANCO"
    rerank_candidates = mock_rerank.call_args.args[1]
    assert ordered.id in {candidate.sku_id for candidate in rerank_candidates}


def test_identify_prefers_the_visual_verdict_over_the_vector_ranking(
    client, courier_token, variants, tmp_path
):
    """Herkennen and boeken must agree: the rerank wins over cosine order."""
    ordered, other, _third, _order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
            (other, 0.94, "reference_images/CALY-ROSSO.jpg", "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=other.id,
            certainty="high",
            distinguishing_feature="label reads Rosso",
            considered_sku_ids=[ordered.id, other.id],
        )

        resp = client.post(
            "/api/receiving/identify",
            files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
            headers=auth_header(courier_token),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku_code"] == "CALY-ROSSO"
    assert body["confidence"] == 0.94


# ---------------------------------------------------------------------------
# select_rerank_candidates — which SKUs reach the visual pass
# ---------------------------------------------------------------------------


def test_candidate_selection_keeps_the_lookalike_cluster_and_drops_the_rest(
    db, sample_org, variants
):
    from app.config import settings
    from app.services.rerank import select_rerank_candidates

    ordered, other, third, _order = variants
    far = _make_sku(db, "SOCKS-1", "Wollen sokken")
    db.commit()

    matches = [
        (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", None),
        (other, 0.94, "reference_images/CALY-ROSSO.jpg", None),
        (third, 0.93, "reference_images/CALY-ROSATO.jpg", None),
        (far, 0.70, "reference_images/SOCKS-1.jpg", None),
    ]

    selected = select_rerank_candidates(db, matches)

    assert [c.sku_code for c in selected] == [
        "CALY-BIANCO",
        "CALY-ROSSO",
        "CALY-ROSATO",
    ]
    assert all(c.image_keys for c in selected)
    assert len(selected) <= settings.rerank_max_candidates


def test_candidate_selection_skips_skus_without_usable_reference_photos(db, variants):
    from app.services.rerank import select_rerank_candidates

    ordered, _other, _third, _order = variants
    no_photo = SKU(sku_code="NOPIC-1", name="Zonder foto")
    db.add(no_photo)
    db.commit()

    selected = select_rerank_candidates(
        db,
        [
            (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", None),
            (no_photo, 0.94, None, None),
        ],
    )

    assert [c.sku_code for c in selected] == ["CALY-BIANCO"]


def test_candidate_selection_reserves_a_place_for_the_best_open_sku(
    db, variants
):
    from app.services.rerank import select_rerank_candidates

    ordered, other, third, _order = variants
    extra_a = _make_sku(db, "CALY-EXTRA-A", "Calycanto Extra A")
    extra_b = _make_sku(db, "CALY-EXTRA-B", "Calycanto Extra B")
    db.commit()

    selected = select_rerank_candidates(
        db,
        [
            (other, 0.95, "reference_images/CALY-ROSSO.jpg", None),
            (third, 0.94, "reference_images/CALY-ROSATO.jpg", None),
            (extra_a, 0.93, "reference_images/CALY-EXTRA-A.jpg", None),
            (extra_b, 0.92, "reference_images/CALY-EXTRA-B.jpg", None),
            # Outside the normal 0.10 band and beyond the four candidate slots,
            # but this is the strongest SKU that can actually be booked.
            (ordered, 0.84, "reference_images/CALY-BIANCO.jpg", None),
        ],
        scope_sku_ids={ordered.id},
    )

    assert len(selected) == 4
    assert selected[0].sku_id == other.id
    assert ordered.id in {candidate.sku_id for candidate in selected}


# ---------------------------------------------------------------------------
# rerank_scan — advisory layer, never fatal
# ---------------------------------------------------------------------------


def test_rerank_never_raises_when_the_vision_call_fails(db, variants, tmp_path):
    from app.services.rerank import rerank_scan, select_rerank_candidates

    ordered, _other, _third, _order = variants
    storage = _tmp_storage(tmp_path)
    storage.save("reference_images/CALY-BIANCO.jpg", FAKE_IMAGE)

    candidates = select_rerank_candidates(
        db, [(ordered, 0.95, "reference_images/CALY-BIANCO.jpg", None)]
    )

    with patch("app.services.rerank.storage", storage), \
         patch("app.services.rerank.get_prompt_required", side_effect=RuntimeError("boom")):
        verdict = asyncio.run(rerank_scan(FAKE_IMAGE, candidates))

    assert verdict.ran is False
    assert verdict.sku_id is None
    assert verdict.is_confident is False


def test_rerank_is_skipped_when_disabled(db, variants):
    from app.services.rerank import rerank_scan

    with patch("app.services.rerank.settings") as mock_settings:
        mock_settings.rerank_enabled = False
        verdict = asyncio.run(rerank_scan(FAKE_IMAGE, []))

    assert verdict.ran is False
    assert verdict.skip_reason == "disabled"
    assert verdict.degraded is True  # wanted but not made


# ---------------------------------------------------------------------------
# needs_visual_check — the close-call trigger
# ---------------------------------------------------------------------------


def test_clear_winner_needs_no_visual_check(db, variants):
    from app.services.rerank import NOT_NEEDED, needs_visual_check

    ordered, other, _third, _order = variants

    needed, reason = needs_visual_check(
        [
            (ordered, 0.95, "a.jpg", None),
            (other, 0.70, "b.jpg", None),
        ],
        scope_sku_ids={ordered.id},
    )

    assert needed is False
    assert reason == NOT_NEEDED


def test_rivals_inside_the_margin_need_a_visual_check(db, variants):
    from app.services.rerank import needs_visual_check

    ordered, other, _third, _order = variants

    needed, _reason = needs_visual_check(
        [
            (ordered, 0.95, "a.jpg", None),
            (other, 0.94, "b.jpg", None),
        ],
        scope_sku_ids={ordered.id},
    )

    assert needed is True


def test_out_of_scope_winner_needs_a_visual_check_even_with_a_wide_gap(db, variants):
    """Wrong box in hand: nothing close to it, but it is not on the order."""
    from app.services.rerank import needs_visual_check

    ordered, other, _third, _order = variants

    needed, _reason = needs_visual_check(
        [
            (other, 0.95, "b.jpg", None),
            (ordered, 0.60, "a.jpg", None),
        ],
        scope_sku_ids={ordered.id},
    )

    assert needed is True


def test_clear_match_skips_the_vision_call_without_flagging_the_scan(
    client, courier_token, variants, tmp_path
):
    """The happy path stays a one-call scan and reaches the picker unflagged."""
    ordered, other, _third, order = variants

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (ordered, 0.95, "reference_images/CALY-BIANCO.jpg", "Calycanto box"),
            (other, 0.60, "reference_images/CALY-ROSSO.jpg", "Wollen sokken"),
        ]

        resp = _post_book(client, courier_token, order)

    assert resp.status_code == 200
    mock_rerank.assert_not_called()
    body = resp.json()
    assert body["sku_code"] == "CALY-BIANCO"
    assert body["manual_review_required"] is False
    # No visual check was wanted, so the picker is never told one is missing.
    # (The short mock description still trips the unrelated quality check.)
    assert "visual check" not in (body["confirmation_reason"] or "")
    assert body["alternatives"] == []
