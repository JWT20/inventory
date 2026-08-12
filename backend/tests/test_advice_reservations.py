"""Pickup reservations owned by wijnadvies1 but held in store inventory."""

from app.config import settings
from app.models import AdviceReservation, InventoryBalance, SKU, StockMovement


API_KEY = "test-advice-write-key"
BASE_URL = "/api/integrations/advice/reservations"


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_sales_api_key", API_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def _bottle(db, org, product_id: str, on_hand: int = 8) -> SKU:
    sku = SKU(
        sku_code=f"{product_id.upper()}-FLES",
        name=product_id,
        organization_id=org.id,
        is_bottle=True,
        source_product_id=product_id,
    )
    db.add(sku)
    db.flush()
    db.add_all(
        [
            InventoryBalance(
                sku_id=sku.id,
                organization_id=org.id,
                inventory_location="store",
                quantity_on_hand=on_hand,
            ),
            InventoryBalance(
                sku_id=sku.id,
                organization_id=org.id,
                inventory_location="warehouse",
                quantity_on_hand=99,
            ),
        ]
    )
    db.commit()
    return sku


def _payload(**overrides) -> dict:
    payload = {
        "external_order_id": "order_123",
        "order_reference": "JUR-2026-000123",
        "fulfillment_method": "pickup",
        "inventory_location": "store",
        "lines": [{"source_product_id": "prd_a", "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def _balance(db, sku_id: int, location: str) -> InventoryBalance:
    return (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku_id, inventory_location=location)
        .one()
    )


def test_reserve_holds_store_stock_only(client, db, sample_org, monkeypatch):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    response = client.post(BASE_URL, json=_payload(), headers=_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["duplicate"] is False
    db.expire_all()
    assert (_balance(db, sku.id, "store").quantity_on_hand,
            _balance(db, sku.id, "store").quantity_reserved) == (8, 2)
    assert (_balance(db, sku.id, "warehouse").quantity_on_hand,
            _balance(db, sku.id, "warehouse").quantity_reserved) == (99, 0)


def test_reserve_is_idempotent_and_rejects_changed_lines(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")

    first = client.post(BASE_URL, json=_payload(), headers=_headers())
    retry = client.post(BASE_URL, json=_payload(), headers=_headers())
    changed = client.post(
        BASE_URL,
        json=_payload(lines=[{"source_product_id": "prd_a", "quantity": 3}]),
        headers=_headers(),
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert changed.status_code == 409
    db.expire_all()
    assert _balance(db, sku.id, "store").quantity_reserved == 2
    assert db.query(AdviceReservation).count() == 1


def test_reserve_is_atomic_for_unknown_or_short_stock(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a", on_hand=1)

    short = client.post(BASE_URL, json=_payload(), headers=_headers())
    unknown = client.post(
        BASE_URL,
        json=_payload(
            external_order_id="order_456",
            lines=[{"source_product_id": "missing", "quantity": 1}],
        ),
        headers=_headers(),
    )

    assert short.status_code == 409
    assert unknown.status_code == 409
    db.expire_all()
    assert _balance(db, sku.id, "store").quantity_reserved == 0
    assert db.query(AdviceReservation).count() == 0


def test_collect_consumes_reservation_and_stock_once(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")
    client.post(BASE_URL, json=_payload(), headers=_headers())

    first = client.post(f"{BASE_URL}/order_123/collect", headers=_headers())
    retry = client.post(f"{BASE_URL}/order_123/collect", headers=_headers())

    assert first.status_code == 200
    assert first.json()["status"] == "collected"
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    db.expire_all()
    balance = _balance(db, sku.id, "store")
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (6, 0)
    movements = db.query(StockMovement).filter_by(sku_id=sku.id).all()
    assert [(row.inventory_location, row.quantity, row.reference_type) for row in movements] == [
        ("store", -2, "advice_pickup")
    ]


def test_release_frees_only_reservation_and_is_idempotent(
    client, db, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)
    sku = _bottle(db, sample_org, "prd_a")
    client.post(BASE_URL, json=_payload(), headers=_headers())

    first = client.post(f"{BASE_URL}/order_123/release", headers=_headers())
    retry = client.post(f"{BASE_URL}/order_123/release", headers=_headers())

    assert first.status_code == 200
    assert first.json()["status"] == "released"
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    db.expire_all()
    balance = _balance(db, sku.id, "store")
    assert (balance.quantity_on_hand, balance.quantity_reserved) == (8, 0)
    assert db.query(StockMovement).filter_by(sku_id=sku.id).count() == 0
