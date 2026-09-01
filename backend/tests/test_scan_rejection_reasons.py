"""A refused scan must say which of five situations the picker is in.

The picker used to get two sentences for five different states: a wine that was
never ordered, a wine whose boxes are all booked, goods that are not in stock
yet, stock that is promised to other customers, and a photo nothing matched.
"Staat niet open in de open orders" covered the first two and "Toewijzingslimiet
bereikt" the next two, so the courier could not tell whether to put the box back
on the pallet, chase the packing slip, or take a better photo.

Each refusal now carries a ``reason_code`` — the same label the trace is scored
with — plus a message that names the wine and the action.
"""

import io

from unittest.mock import patch

import pytest

from app.models import Customer, InventoryBalance, OrderLine
from app.services.rerank import RerankVerdict
from tests.conftest import auth_header
from tests.test_lookalike_matching import (
    _make_open_order,
    _make_sku,
    _mock_process_package,
    _post_book,
    _tmp_storage,
)
from tests.test_wine_filter import FAKE_IMAGE


def _scan(client, courier_token, order, tmp_path, matches, verdict=None):
    """Run one scan with the vector search and visual pass stubbed."""
    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = matches
        mock_rerank.return_value = verdict or RerankVerdict(ran=False, skip_reason="clear match")
        return _post_book(client, courier_token, order)


def _ref(sku):
    return f"reference_images/{sku.sku_code}.jpg"


def _add_open_line(db, org, order, sku, *, customer_name="Struis", quantity=1):
    """A second wine still open on the same order, so the week is not empty."""
    customer = Customer(name=customer_name, organization_id=org.id)
    db.add(customer)
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


def test_all_boxes_already_booked_says_the_box_is_left_over(
    client, courier_token, db, sample_org, tmp_path
):
    """Everybody who ordered this wine has their boxes: the doos is simply over.

    Not "this customer is done" — as long as anyone still needs the wine the
    scan is booked on them instead, and no message appears at all.
    """
    ordered = _make_sku(db, "LARE-TOUR", "La Renaudie Touraine Sauvignon")
    order = _make_open_order(db, sample_org, ordered, quantity=1)
    line = db.query(OrderLine).filter(OrderLine.sku_id == ordered.id).one()
    line.booked_count = 1  # the one box that was ordered came in earlier today
    # The week is still being picked, so the scan scope itself is not empty.
    _add_open_line(db, sample_org, order, _make_sku(db, "OTHE-WINE", "Ander wijntje"))
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[(ordered, 0.98, _ref(ordered), "Sauvignon box")],
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "sku_full"
    assert "Al compleet" in detail["message"]
    assert "LARE-TOUR" in detail["message"]
    assert "blijft over" in detail["message"]


def test_another_customer_still_needs_it_so_the_scan_is_simply_booked(
    client, courier_token, db, sample_org, tmp_path
):
    """One customer being complete is not a refusal — the box goes to the next."""
    ordered = _make_sku(db, "LARE-TOUR", "La Renaudie Touraine Sauvignon")
    order = _make_open_order(db, sample_org, ordered, quantity=1)
    full_line = db.query(OrderLine).filter(OrderLine.sku_id == ordered.id).one()
    full_line.booked_count = 1

    second = Customer(name="De Haan", organization_id=sample_org.id)
    db.add(second)
    db.flush()
    db.add(
        OrderLine(
            order_id=order.id,
            sku_id=ordered.id,
            customer_id=second.id,
            klant=second.name,
            quantity=1,
            booked_count=0,
            delivery_day="wednesday",
        )
    )
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[(ordered, 0.98, _ref(ordered), "Sauvignon box")],
    )

    assert resp.status_code == 200
    assert resp.json()["klant"] == "De Haan"


def test_wine_that_was_never_ordered_tells_the_picker_to_set_it_aside(
    client, courier_token, db, sample_org, tmp_path
):
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    stranger = _make_sku(db, "TERR-GAUD", "Terras Gauda O Rosal")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[
            (stranger, 0.94, _ref(stranger), "Terras Gauda box"),
            (ordered, 0.77, _ref(ordered), "Calycanto box"),
        ],
        verdict=RerankVerdict(
            ran=True,
            sku_id=stranger.id,
            certainty="high",
            distinguishing_feature="doos zegt Terras Gauda",
            considered_sku_ids=[stranger.id, ordered.id],
        ),
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "not_ordered"
    assert "Niet besteld deze week" in detail["message"]
    assert "TERR-GAUD" in detail["message"]


def test_ad_hoc_order_rejection_names_the_order_instead_of_the_week(
    client, courier_token, db, sample_org, tmp_path
):
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    stranger = _make_sku(db, "TERR-GAUD", "Terras Gauda O Rosal")
    order = _make_open_order(db, sample_org, ordered)
    order.delivery_week = None
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[
            (stranger, 0.94, _ref(stranger), "Terras Gauda box"),
            (ordered, 0.77, _ref(ordered), "Calycanto box"),
        ],
        verdict=RerankVerdict(
            ran=True,
            sku_id=stranger.id,
            certainty="high",
            considered_sku_ids=[stranger.id, ordered.id],
        ),
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "not_ordered"
    assert "Niet besteld in deze order" in detail["message"]
    assert "deze week" not in detail["message"]


def test_no_stock_yet_points_at_the_packing_slip(
    client, courier_token, db, sample_org, tmp_path
):
    """Zero stock made the allocation cap collapse to "limit reached".

    That sentence sent couriers looking for an allocation problem that does not
    exist: the goods are recognised and ordered, they are just not booked into
    stock yet.
    """
    ordered = _make_sku(db, "TALM-CREM", "Talmard Cremant de Bourgogne")
    order = _make_open_order(db, sample_org, ordered)
    balance = db.query(InventoryBalance).filter(InventoryBalance.sku_id == ordered.id).one()
    balance.quantity_on_hand = 0
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[(ordered, 0.93, _ref(ordered), "Talmard box")],
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "no_stock"
    assert "Pakbon nog niet verwerkt" in detail["message"]
    assert "TALM-CREM" in detail["message"]


def test_stock_promised_to_others_is_its_own_message(
    client, courier_token, db, sample_org, tmp_path
):
    """Stock exists but the allocation hands none of it to this scope."""
    ordered = _make_sku(db, "TARA-PINO", "Tarapaca Pinot Grigio")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    with patch("app.routers.receiving.compute_allocation", return_value={}):
        resp = _scan(
            client, courier_token, order, tmp_path,
            matches=[(ordered, 0.93, _ref(ordered), "Tarapaca box")],
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "cap_reached"
    assert "Alles verdeeld" in detail["message"]


def test_fully_reserved_stock_is_distributed_not_missing(
    client, courier_token, db, sample_org, tmp_path
):
    """Physical stock exists, so a zero available balance is not a missing pakbon."""
    ordered = _make_sku(db, "TARA-RES", "Tarapaca Reserva")
    order = _make_open_order(db, sample_org, ordered, quantity=3)
    balance = db.query(InventoryBalance).filter(InventoryBalance.sku_id == ordered.id).one()
    balance.quantity_on_hand = 3
    balance.quantity_reserved = 3
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[(ordered, 0.93, _ref(ordered), "Tarapaca box")],
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "cap_reached"
    assert "Alles verdeeld" in detail["message"]
    assert "Pakbon" not in detail["message"]


def test_nothing_recognised_tells_the_picker_how_to_reshoot(
    client, courier_token, db, sample_org, tmp_path
):
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[(ordered, 0.42, _ref(ordered), "Calycanto box")],
    )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "not_recognized"
    assert "verder af" in detail["message"]


def test_the_trace_carries_what_the_decision_rested_on(
    client, courier_token, db, sample_org, tmp_path, monkeypatch
):
    """Reading a refusal back used to mean lining up three observations by hand.

    Scope best, catalogue best, the gap between them and the visual verdict now
    travel with the scan, and the reason code the picker saw is the one the
    trace is scored with.
    """
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    stranger = _make_sku(db, "TERR-GAUD", "Terras Gauda O Rosal")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    recorded: list[dict] = []
    monkeypatch.setattr(
        "app.routers.receiving.record_scan", lambda **kwargs: recorded.append(kwargs)
    )

    captured: dict = {}
    original = None

    from app.routers import receiving

    original = receiving._record_scan_outcome

    def _capture(outcome, *, session_id):
        captured["decision"] = outcome.decision
        captured["reason_code"] = outcome.reason_code
        return original(outcome, session_id=session_id)

    monkeypatch.setattr(receiving, "_record_scan_outcome", _capture)

    resp = _scan(
        client, courier_token, order, tmp_path,
        matches=[
            (stranger, 0.94, _ref(stranger), "Terras Gauda box"),
            (ordered, 0.77, _ref(ordered), "Calycanto box"),
        ],
        verdict=RerankVerdict(
            ran=True,
            sku_id=stranger.id,
            certainty="high",
            distinguishing_feature="doos zegt Terras Gauda",
            considered_sku_ids=[stranger.id, ordered.id],
        ),
    )

    assert resp.status_code == 409
    decision = captured["decision"]
    assert captured["reason_code"] == "not_ordered" == decision["outcome"]
    assert decision["catalogue_best"]["sku_code"] == "TERR-GAUD"
    assert decision["scope_best"]["sku_code"] == "CALY-BIANCO"
    assert decision["gap"] == pytest.approx(0.17)
    assert decision["rerank"]["agreed_with_vector"] is True
    assert decision["scan_image_url"]

    assert recorded[0]["reason_code"] == "not_ordered"
    assert recorded[0]["rejected"] is True
    assert set(recorded[0]["candidate_sku_ids"]) == {stranger.id, ordered.id}


def test_a_refusal_does_not_travel_as_an_exception(
    courier_user, db, sample_org, tmp_path
):
    """The traced scan must *return* its refusal.

    An exception crossing the span is what marked "wrong box" as an ERROR next
    to real outages, and that is what made the error figures unreadable.
    """
    import asyncio

    from app.routers.receiving import _scan_and_book

    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    stranger = _make_sku(db, "TERR-GAUD", "Terras Gauda O Rosal")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    class _Upload:
        filename = "box.jpg"
        file = io.BytesIO(FAKE_IMAGE)

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_package), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)), \
         patch("app.routers.receiving.find_best_matches") as mock_match, \
         patch("app.routers.receiving.rerank_scan") as mock_rerank:
        mock_match.return_value = [
            (stranger, 0.94, _ref(stranger), "Terras Gauda box"),
            (ordered, 0.77, _ref(ordered), "Calycanto box"),
        ]
        mock_rerank.return_value = RerankVerdict(
            ran=True,
            sku_id=stranger.id,
            certainty="high",
            considered_sku_ids=[stranger.id, ordered.id],
        )
        outcome = asyncio.run(
            _scan_and_book(
                file=_Upload(),
                order_id=order.id,
                scan_mode="box",
                db=db,
                user=courier_user,
            )
        )

    assert outcome.rejected
    assert outcome.rejection.reason_code == "not_ordered"
    assert outcome.rejection.status_code == 409
    assert outcome.confirmation is None


def test_not_a_package_keeps_its_reason_code(
    client, courier_token, db, sample_org, tmp_path
):
    ordered = _make_sku(db, "CALY-BIANCO", "Calycanto Bianco")
    order = _make_open_order(db, sample_org, ordered)
    db.commit()

    async def _mock_process_bottle(_image_bytes):
        return "een losse fles op tafel", [0.1] * 3072, False

    with patch("app.routers.receiving.process_image", side_effect=_mock_process_bottle), \
         patch("app.routers.receiving.storage", _tmp_storage(tmp_path)):
        resp = client.post(
            "/api/receiving/book",
            files={"file": ("box.jpg", io.BytesIO(FAKE_IMAGE), "image/jpeg")},
            data={"order_id": str(order.id), "scan_mode": "box"},
            headers=auth_header(courier_token),
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "not_a_package"
