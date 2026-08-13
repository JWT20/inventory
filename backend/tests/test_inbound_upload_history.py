from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.models import (
    InboundUploadAttempt,
    InventoryBalance,
    Organization,
    SKU,
    StockMovement,
)
from tests.conftest import auth_header


class _TmpStorage:
    def __init__(self, base):
        self.base = base

    def save(self, key: str, content: bytes) -> str:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def url(self, key: str) -> str:
        return f"/api/files/{key}"


def test_extract_preview_creates_visible_upload_attempt(
    client, db, owner_token, owner_user, tmp_path
):
    mocked = {
        "supplier_name": "Vojacek",
        "reference": "PKB-HISTORY-1",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": "UNKNOWN-1",
                "description": "Unknown wine",
                "quantity": 6,
                "quantity_unit": "pieces",
                "confidence": 0.95,
            }
        ],
    }

    with patch(
        "app.routers.inventory.extract_shipment_document",
        new=AsyncMock(return_value=mocked),
    ), patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        response = client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("vojacek.pdf", b"%PDF-fake", "application/pdf")},
        )

    assert response.status_code == 200, response.text
    attempt_id = response.json()["upload_attempt_id"]
    attempt = db.get(InboundUploadAttempt, attempt_id)
    assert attempt.organization_id == owner_user.organization_id
    assert attempt.original_filename == "vojacek.pdf"
    assert attempt.reference == "PKB-HISTORY-1"
    assert attempt.status == "needs_action"
    assert attempt.line_count == 1
    assert attempt.bookable_line_count == 0

    history = client.get(
        "/api/inbound-uploads",
        headers=auth_header(owner_token),
    )
    assert history.status_code == 200
    assert history.json()[0]["id"] == attempt_id
    assert history.json()[0]["status"] == "needs_action"


def test_empty_upload_is_recorded_as_failed(client, db, owner_token):
    response = client.post(
        "/api/shipments/extract-preview",
        headers=auth_header(owner_token),
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    attempt = db.query(InboundUploadAttempt).one()
    assert attempt.status == "failed"
    assert attempt.error_stage == "extraction"
    assert attempt.error_message == "Leeg bestand"


def test_upload_attempt_tracks_draft_and_successful_stock_booking(
    client, db, owner_token, owner_user
):
    sku = SKU(
        sku_code="HISTORY-SKU",
        name="History wine",
        organization_id=owner_user.organization_id,
    )
    db.add(sku)
    db.flush()
    attempt = InboundUploadAttempt(
        organization_id=owner_user.organization_id,
        uploaded_by=owner_user.id,
        source_type="file",
        original_filename="history.pdf",
        document_sha256="a" * 64,
        supplier_name="Vojacek",
        reference="PKB-HISTORY-2",
        status="needs_action",
        line_count=1,
        bookable_line_count=1,
    )
    db.add(attempt)
    db.commit()

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Vojacek",
            "reference": "PKB-HISTORY-2",
            "document_sha256": "a" * 64,
            "upload_attempt_id": attempt.id,
            "lines": [
                {"sku_id": sku.id, "quantity": 4, "supplier_code": "SUP-HISTORY"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    shipment_id = created.json()["id"]
    db.refresh(attempt)
    assert attempt.status == "draft"
    assert attempt.shipment_id == shipment_id

    booked = client.post(
        f"/api/shipments/{shipment_id}/book",
        headers=auth_header(owner_token),
    )
    assert booked.status_code == 200, booked.text
    db.refresh(attempt)
    assert attempt.status == "booked"
    assert attempt.booked_line_count == 1
    assert attempt.booked_quantity == 4

    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert balance.quantity_on_hand == 4
    movement = db.query(StockMovement).filter_by(
        reference_type="shipment",
        reference_id=shipment_id,
    ).one()
    assert movement.quantity == 4
    history = client.get(
        "/api/inbound-uploads",
        headers=auth_header(owner_token),
    )
    assert history.status_code == 200, history.text
    assert history.json()[0]["booked_skus"] == [
        {
            "sku_id": sku.id,
            "sku_code": "HISTORY-SKU",
            "sku_name": "History wine",
            "quantity": 4,
            "is_bottle": False,
        }
    ]


def test_upload_history_groups_booked_lines_and_preserves_units(
    client, db, owner_token, owner_user
):
    box_sku = SKU(
        sku_code="HISTORY-BOX",
        name="Case wine",
        organization_id=owner_user.organization_id,
    )
    bottle_sku = SKU(
        sku_code="HISTORY-BOTTLE",
        name="Loose bottle",
        organization_id=owner_user.organization_id,
        is_bottle=True,
    )
    db.add_all([box_sku, bottle_sku])
    db.flush()
    attempt = InboundUploadAttempt(
        organization_id=owner_user.organization_id,
        uploaded_by=owner_user.id,
        source_type="text",
        status="needs_action",
        line_count=3,
        bookable_line_count=3,
    )
    db.add(attempt)
    db.commit()

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "upload_attempt_id": attempt.id,
            "lines": [
                {"sku_id": box_sku.id, "quantity": 2},
                {"sku_id": box_sku.id, "quantity": 3},
                {"sku_id": bottle_sku.id, "quantity": 4},
            ],
        },
    )
    assert created.status_code == 201, created.text

    booked = client.post(
        f'/api/shipments/{created.json()["id"]}/book',
        headers=auth_header(owner_token),
    )
    assert booked.status_code == 200, booked.text
    assert [line["is_bottle"] for line in booked.json()["lines"]] == [
        False,
        False,
        True,
    ]
    assert booked.json()["booked_skus"] == [
        {
            "sku_id": box_sku.id,
            "sku_code": "HISTORY-BOX",
            "sku_name": "Case wine",
            "quantity": 5,
            "is_bottle": False,
        },
        {
            "sku_id": bottle_sku.id,
            "sku_code": "HISTORY-BOTTLE",
            "sku_name": "Loose bottle",
            "quantity": 4,
            "is_bottle": True,
        },
    ]

    history = client.get(
        "/api/inbound-uploads",
        headers=auth_header(owner_token),
    )
    assert history.status_code == 200, history.text
    assert history.json()[0]["booked_skus"] == [
        {
            "sku_id": box_sku.id,
            "sku_code": "HISTORY-BOX",
            "sku_name": "Case wine",
            "quantity": 5,
            "is_bottle": False,
        },
        {
            "sku_id": bottle_sku.id,
            "sku_code": "HISTORY-BOTTLE",
            "sku_name": "Loose bottle",
            "quantity": 4,
            "is_bottle": True,
        },
    ]


def test_missing_upload_attempt_id_recovers_unique_recent_hash_match(
    client, db, owner_token, owner_user
):
    sku = SKU(
        sku_code="HISTORY-RECOVERED",
        name="Recovered history wine",
        organization_id=owner_user.organization_id,
    )
    db.add(sku)
    db.flush()
    attempt = InboundUploadAttempt(
        organization_id=owner_user.organization_id,
        uploaded_by=owner_user.id,
        source_type="text",
        document_sha256="b" * 64,
        supplier_name="Anfors-Imperial",
        status="needs_action",
        line_count=2,
        bookable_line_count=1,
    )
    db.add(attempt)
    db.commit()

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Anfors-Imperial",
            "document_sha256": "b" * 64,
            # Simulate a stale frontend that does not send upload_attempt_id.
            "lines": [{"sku_id": sku.id, "quantity": 3, "supplier_code": "RECOVERED"}],
        },
    )

    assert created.status_code == 201, created.text
    db.refresh(attempt)
    assert attempt.shipment_id == created.json()["id"]
    assert attempt.status == "draft"
    assert attempt.bookable_line_count == 1

    booked = client.post(
        f'/api/shipments/{created.json()["id"]}/book',
        headers=auth_header(owner_token),
    )
    assert booked.status_code == 200, booked.text
    db.refresh(attempt)
    assert attempt.status == "booked"
    assert attempt.booked_line_count == 1
    assert attempt.booked_quantity == 3


def test_missing_upload_attempt_id_does_not_guess_between_duplicate_attempts(
    client, db, owner_token, owner_user
):
    sku = SKU(
        sku_code="HISTORY-AMBIGUOUS",
        name="Ambiguous history wine",
        organization_id=owner_user.organization_id,
    )
    db.add(sku)
    db.flush()
    attempts = [
        InboundUploadAttempt(
            organization_id=owner_user.organization_id,
            uploaded_by=owner_user.id,
            source_type="text",
            document_sha256="c" * 64,
            status="needs_action",
            line_count=1,
        )
        for _ in range(2)
    ]
    db.add_all(attempts)
    db.commit()

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "supplier_name": "Anfors-Imperial",
            "document_sha256": "c" * 64,
            "lines": [{"sku_id": sku.id, "quantity": 1}],
        },
    )

    assert created.status_code == 201, created.text
    for attempt in attempts:
        db.refresh(attempt)
        assert attempt.shipment_id is None
        assert attempt.status == "needs_action"


def test_upload_history_is_scoped_and_not_available_to_customers(
    client, db, owner_token, customer_token, owner_user
):
    other_org = Organization(name="Other", slug="other-history")
    db.add(other_org)
    db.flush()
    db.add_all(
        [
            InboundUploadAttempt(
                organization_id=owner_user.organization_id,
                uploaded_by=owner_user.id,
                source_type="text",
                status="needs_action",
                reference="MINE",
            ),
            InboundUploadAttempt(
                organization_id=other_org.id,
                source_type="file",
                status="failed",
                reference="OTHER",
            ),
        ]
    )
    db.commit()

    history = client.get(
        "/api/inbound-uploads",
        headers=auth_header(owner_token),
    )
    assert history.status_code == 200
    assert [row["reference"] for row in history.json()] == ["MINE"]

    forbidden = client.get(
        "/api/inbound-uploads",
        headers=auth_header(customer_token),
    )
    assert forbidden.status_code == 403


def test_stale_processing_attempt_is_marked_failed(db, owner_user):
    from app.routers.inventory import sweep_stale_inbound_uploads

    attempt = InboundUploadAttempt(
        organization_id=owner_user.organization_id,
        uploaded_by=owner_user.id,
        source_type="file",
        status="processing",
        updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30),
    )
    db.add(attempt)
    db.commit()

    assert sweep_stale_inbound_uploads(db) == 1
    db.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.error_message == "Verwerking onderbroken"


def test_history_shows_which_pool_the_goods_landed_in(client, db, owner_token, sample_org):
    """A pakbon can go to the shop or the warehouse; afterwards you must be
    able to see which. Without it the only way to tell is to go count."""
    sku = SKU(sku_code="HISTORY-LOC", name="Locatiewijn", organization_id=sample_org.id)
    db.add(sku)
    db.commit()

    def _book(location: str, reference: str) -> dict:
        attempt = InboundUploadAttempt(
            organization_id=sample_org.id,
            source_type="text",
            status="needs_action",
            line_count=1,
            bookable_line_count=1,
        )
        db.add(attempt)
        db.commit()
        created = client.post(
            "/api/shipments",
            headers=auth_header(owner_token),
            json={
                "upload_attempt_id": attempt.id,
                "supplier_name": "Vojacek",
                "reference": reference,
                "inventory_location": location,
                "lines": [{"sku_id": sku.id, "quantity": 2}],
            },
        )
        assert created.status_code == 201, created.text
        booked = client.post(
            f'/api/shipments/{created.json()["id"]}/book',
            headers=auth_header(owner_token),
        )
        assert booked.status_code == 200, booked.text
        return created.json()

    store_shipment = _book("store", "PKB-WINKEL")
    warehouse_shipment = _book("warehouse", "PKB-MAGAZIJN")

    history = client.get("/api/inbound-uploads", headers=auth_header(owner_token))

    assert history.status_code == 200, history.text
    by_shipment = {
        row["shipment_id"]: row["inventory_location"] for row in history.json()
    }
    assert by_shipment[store_shipment["id"]] == "store"
    assert by_shipment[warehouse_shipment["id"]] == "warehouse"


def test_history_reports_no_location_when_nothing_was_booked(
    client, db, owner_token, tmp_path
):
    """An attempt that never produced a pakbon booked nothing anywhere, so
    naming a location would be a guess."""
    with patch("app.routers.inventory.storage", _TmpStorage(tmp_path)):
        client.post(
            "/api/shipments/extract-preview",
            headers=auth_header(owner_token),
            files={"file": ("leeg.pdf", b"", "application/pdf")},
        )

    history = client.get("/api/inbound-uploads", headers=auth_header(owner_token))

    assert history.status_code == 200
    assert history.json()[0]["shipment_id"] is None
    assert history.json()[0]["inventory_location"] is None


def test_history_hides_location_while_shipment_is_still_a_draft(
    client, db, owner_token, sample_org
):
    """Choosing a destination on a draft does not mean stock landed there."""
    sku = SKU(
        sku_code="HISTORY-DRAFT-LOC",
        name="Conceptlocatiewijn",
        organization_id=sample_org.id,
    )
    attempt = InboundUploadAttempt(
        organization_id=sample_org.id,
        source_type="text",
        status="needs_action",
        line_count=1,
        bookable_line_count=1,
    )
    db.add_all([sku, attempt])
    db.commit()

    created = client.post(
        "/api/shipments",
        headers=auth_header(owner_token),
        json={
            "upload_attempt_id": attempt.id,
            "supplier_name": "Vojacek",
            "reference": "PKB-CONCEPT-WINKEL",
            "inventory_location": "store",
            "lines": [{"sku_id": sku.id, "quantity": 2}],
        },
    )
    assert created.status_code == 201, created.text

    history = client.get("/api/inbound-uploads", headers=auth_header(owner_token))

    assert history.status_code == 200, history.text
    row = next(item for item in history.json() if item["shipment_id"] == created.json()["id"])
    assert row["status"] == "draft"
    assert row["inventory_location"] is None
