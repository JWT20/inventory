"""Sale reports from the advice app (shop counter + webshop)."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import AdviceSale, InventoryBalance, Organization, SKU, StockMovement


API_KEY = "test-advice-sales-key"
URL = "/api/integrations/advice/sales"


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_sales_api_key", API_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers(key: str = API_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _bottle(db, org, *, product_id: str, code: str, on_hand: int = 10) -> SKU:
    sku = SKU(
        sku_code=code,
        name=code,
        organization_id=org.id,
        is_bottle=True,
        source_product_id=product_id,
    )
    db.add(sku)
    db.flush()
    db.add(
        InventoryBalance(
            sku_id=sku.id, organization_id=org.id, quantity_on_hand=on_hand
        )
    )
    db.commit()
    return sku


def _payload(**overrides) -> dict:
    payload = {
        "sale_id": "ord_1",
        "channel": "pos",
        "lines": [{"source_product_id": "prd_a", "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def test_requires_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "advice_sales_api_key", "")
    monkeypatch.setattr(settings, "advice_stock_organization_id", None)

    response = client.post(URL, json=_payload(), headers=_headers())

    assert response.status_code == 503


def test_rejects_missing_or_invalid_key(client, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)

    missing = client.post(URL, json=_payload())
    invalid = client.post(URL, json=_payload(), headers=_headers("wrong-key"))

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_stock_key_cannot_post_sales(client, sample_org, monkeypatch):
    """The read and write directions are independently revocable."""
    _configure(monkeypatch, sample_org.id)
    monkeypatch.setattr(settings, "advice_stock_api_key", "read-only-key")

    response = client.post(URL, json=_payload(), headers=_headers("read-only-key"))

    assert response.status_code == 401


def test_books_sale_off_stock(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    response = client.post(URL, json=_payload(), headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == [
        {
            "source_product_id": "prd_a",
            "sku_code": "BAROLO-750-FLES",
            "quantity": 2,
            "quantity_available": 8,
        }
    ]
    assert body["duplicate"] == []
    assert body["unmatched"] == []

    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 8
    movement = db.query(StockMovement).filter(StockMovement.sku_id == sku.id).one()
    assert movement.movement_type == "sale"
    assert movement.quantity == -2
    assert movement.reference_type == "advice_sale"


def test_retry_is_idempotent(client, db, sample_org, monkeypatch):
    """A counter on bad wifi retries; the bottles must leave stock once."""
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    first = client.post(URL, json=_payload(), headers=_headers())
    second = client.post(URL, json=_payload(), headers=_headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["applied"] == []
    assert second.json()["duplicate"] == ["prd_a"]

    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 8
    assert db.query(StockMovement).filter(StockMovement.sku_id == sku.id).count() == 1
    assert db.query(AdviceSale).count() == 1


def test_unknown_product_does_not_block_the_rest(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    known = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    response = client.post(
        URL,
        json=_payload(
            lines=[
                {"source_product_id": "prd_a", "quantity": 1},
                {"source_product_id": "prd_unknown", "quantity": 3},
            ]
        ),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert [line["source_product_id"] for line in body["applied"]] == ["prd_a"]
    assert body["unmatched"] == ["prd_unknown"]

    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == known.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 9


def test_relinked_product_is_booked_on_retry(client, db, sample_org, monkeypatch):
    """An unmatched line is absent, not booked — a later retry still books it."""
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")
    lines = [
        {"source_product_id": "prd_a", "quantity": 1},
        {"source_product_id": "prd_b", "quantity": 1},
    ]

    client.post(URL, json=_payload(lines=lines), headers=_headers())
    late = _bottle(db, sample_org, product_id="prd_b", code="CHIANTI-750-FLES")
    second = client.post(URL, json=_payload(lines=lines), headers=_headers())

    body = second.json()
    assert [line["source_product_id"] for line in body["applied"]] == ["prd_b"]
    assert body["duplicate"] == ["prd_a"]

    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == late.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 9


def test_sale_may_take_stock_negative(client, db, sample_org, monkeypatch):
    """The customer already walked out with the bottle; never refuse the report."""
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES", on_hand=1)

    response = client.post(
        URL,
        json=_payload(lines=[{"source_product_id": "prd_a", "quantity": 3}]),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["applied"][0]["quantity_available"] == 0
    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == -2


def test_return_adds_stock_back(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    response = client.post(
        URL,
        json=_payload(
            sale_id="ord_1_refund",
            lines=[{"source_product_id": "prd_a", "quantity": -2}],
        ),
        headers=_headers(),
    )

    assert response.status_code == 200
    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 12


def test_repeated_product_lines_are_collapsed(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    response = client.post(
        URL,
        json=_payload(
            lines=[
                {"source_product_id": "prd_a", "quantity": 1},
                {"source_product_id": "prd_a", "quantity": 2},
            ]
        ),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["applied"][0]["quantity"] == 3
    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == sku.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 7


def test_other_organization_bottles_are_invisible(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    other = Organization(name="Andere handelaar", slug="andere-handelaar")
    db.add(other)
    db.flush()
    foreign = _bottle(db, other, product_id="prd_a", code="VREEMD-750-FLES")

    response = client.post(URL, json=_payload(), headers=_headers())

    assert response.status_code == 200
    assert response.json()["unmatched"] == ["prd_a"]
    balance = (
        db.query(InventoryBalance).filter(InventoryBalance.sku_id == foreign.id).one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 10


def test_box_sku_can_never_carry_an_advice_product(db, sample_org):
    """Only bottles are shared with the advice app; boxes stay a B2B flow.

    Guarded by ck_skus_advice_product_is_bottle, so a sale can never resolve to
    a box even before the endpoint's own is_bottle filter runs.
    """
    db.add(
        SKU(
            sku_code="BAROLO-DOOS",
            name="Barolo doos",
            organization_id=sample_org.id,
            is_bottle=False,
            source_product_id="prd_a",
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_zero_quantity_is_rejected(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    _bottle(db, sample_org, product_id="prd_a", code="BAROLO-750-FLES")

    response = client.post(
        URL,
        json=_payload(lines=[{"source_product_id": "prd_a", "quantity": 0}]),
        headers=_headers(),
    )

    assert response.status_code == 422
