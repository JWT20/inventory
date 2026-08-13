"""Moving stock between the warehouse and the shop as one booking."""

from app.models import InventoryBalance, SKU, StockMovement
from tests.conftest import auth_header


URL = "/api/inventory/transfer"


def _stocked(db, org, *, warehouse=10, store=0, reserved=0) -> SKU:
    sku = SKU(sku_code="MOVE-WINE", name="Verplaatswijn", organization_id=org.id)
    db.add(sku)
    db.flush()
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=org.id,
            inventory_location="warehouse",
            quantity_on_hand=warehouse,
            quantity_reserved=reserved,
        )
    )
    if store:
        db.add(
            InventoryBalance(
                sku_id=sku.id,
                organization_id=org.id,
                inventory_location="store",
                quantity_on_hand=store,
            )
        )
    db.commit()
    return sku


def _balance(db, sku_id, location):
    return (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku_id, inventory_location=location)
        .one_or_none()
    )


def _body(sku, **overrides):
    body = {
        "sku_id": sku.id,
        "quantity": 6,
        "from_location": "warehouse",
        "to_location": "store",
    }
    body.update(overrides)
    return body


def test_transfer_moves_stock_and_links_both_halves(
    client, db, owner_token, sample_org
):
    sku = _stocked(db, sample_org, warehouse=10)

    resp = client.post(URL, json=_body(sku), headers=auth_header(owner_token))

    assert resp.status_code == 200, resp.text
    assert {
        (b["inventory_location"], b["quantity_on_hand"])
        for b in resp.json()["balances"]
    } == {("store", 6), ("warehouse", 4)}
    db.expire_all()
    assert _balance(db, sku.id, "warehouse").quantity_on_hand == 4
    assert _balance(db, sku.id, "store").quantity_on_hand == 6

    movements = db.query(StockMovement).filter_by(sku_id=sku.id).all()
    assert {(m.inventory_location, m.quantity) for m in movements} == {
        ("warehouse", -6),
        ("store", 6),
    }
    assert all(m.movement_type == "transfer" for m in movements)
    # Each leg names the other, so the log can never show one half alone.
    by_id = {m.id: m for m in movements}
    for movement in movements:
        assert by_id[movement.reference_id].reference_id == movement.id


def test_transfer_works_back_to_the_warehouse(client, db, owner_token, sample_org):
    sku = _stocked(db, sample_org, warehouse=2, store=5)

    resp = client.post(
        URL,
        json=_body(sku, quantity=3, from_location="store", to_location="warehouse"),
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert _balance(db, sku.id, "store").quantity_on_hand == 2
    assert _balance(db, sku.id, "warehouse").quantity_on_hand == 5


def test_transfer_never_touches_stock_promised_to_someone_else(
    client, db, owner_token, sample_org
):
    """Reserved bottles are already spoken for, so they cannot be moved away."""
    sku = _stocked(db, sample_org, warehouse=10, reserved=7)

    resp = client.post(
        URL, json=_body(sku, quantity=6), headers=auth_header(owner_token)
    )

    assert resp.status_code == 409
    db.expire_all()
    assert _balance(db, sku.id, "warehouse").quantity_on_hand == 10
    # Nothing half-happened: no destination row, no movements at all.
    assert _balance(db, sku.id, "store") is None
    assert db.query(StockMovement).filter_by(sku_id=sku.id).count() == 0


def test_transfer_refuses_impossible_requests(client, db, owner_token, sample_org):
    sku = _stocked(db, sample_org, warehouse=4)

    too_much = client.post(
        URL, json=_body(sku, quantity=99), headers=auth_header(owner_token)
    )
    same_place = client.post(
        URL,
        json=_body(sku, to_location="warehouse"),
        headers=auth_header(owner_token),
    )
    nothing = client.post(
        URL, json=_body(sku, quantity=0), headers=auth_header(owner_token)
    )

    assert too_much.status_code == 409
    assert same_place.status_code == 422
    assert nothing.status_code == 422
    db.expire_all()
    assert _balance(db, sku.id, "warehouse").quantity_on_hand == 4
    assert db.query(StockMovement).filter_by(sku_id=sku.id).count() == 0


def test_couriers_may_not_move_goods_onto_the_shop_shelf(
    client, db, courier_token, sample_org
):
    sku = _stocked(db, sample_org, warehouse=10)

    resp = client.post(
        URL,
        json=_body(sku, organization_id=sample_org.id),
        headers=auth_header(courier_token),
    )

    assert resp.status_code == 403
    db.expire_all()
    assert _balance(db, sku.id, "warehouse").quantity_on_hand == 10
