"""The merchant's view on stock the advice app is holding."""

from app.auth import hash_password
from app.models import (
    AdviceReservation,
    AdviceReservationLine,
    InventoryBalance,
    Organization,
    SKU,
    User,
)
from tests.conftest import auth_header, create_token


BASE_URL = "/api/advice-reservations"


def _bottle(db, org, code: str, *, on_hand: int = 8, reserved: int = 0) -> SKU:
    sku = SKU(
        sku_code=code,
        name=f"Wijn {code}",
        organization_id=org.id,
        is_bottle=True,
        source_product_id=f"prd_{code.lower()}",
    )
    db.add(sku)
    db.flush()
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=org.id,
            inventory_location="store",
            quantity_on_hand=on_hand,
            quantity_reserved=reserved,
        )
    )
    db.commit()
    return sku


def _reservation(db, org, sku, *, order_id: str, quantity: int = 2, status="active"):
    reservation = AdviceReservation(
        organization_id=org.id,
        external_order_id=order_id,
        order_reference=f"JUR-2026-{order_id}",
        fulfillment_method="pickup",
        inventory_location="store",
        status=status,
    )
    db.add(reservation)
    db.flush()
    db.add(
        AdviceReservationLine(
            reservation_id=reservation.id, sku_id=sku.id, quantity=quantity
        )
    )
    db.commit()
    db.refresh(reservation)
    return reservation


def test_merchant_sees_which_order_holds_which_bottles(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "HOLD-A", reserved=2)
    _reservation(db, sample_org, sku, order_id="order_a")

    resp = client.get(BASE_URL, headers=auth_header(owner_token))

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["order_reference"] == "JUR-2026-order_a"
    assert rows[0]["total_quantity"] == 2
    assert rows[0]["lines"][0]["sku_code"] == "HOLD-A"


def test_listing_defaults_to_active_and_can_widen(
    client, db, owner_token, sample_org
):
    sku = _bottle(db, sample_org, "HOLD-B", reserved=2)
    _reservation(db, sample_org, sku, order_id="order_open")
    _reservation(db, sample_org, sku, order_id="order_done", status="collected")

    default = client.get(BASE_URL, headers=auth_header(owner_token))
    everything = client.get(f"{BASE_URL}?status=all", headers=auth_header(owner_token))
    bogus = client.get(f"{BASE_URL}?status=zweeft", headers=auth_header(owner_token))

    assert [row["external_order_id"] for row in default.json()] == ["order_open"]
    assert len(everything.json()) == 2
    assert bogus.status_code == 400




def test_the_view_is_read_only(client, db, owner_token, sample_org):
    """Lifting a hold here would desync the advice app, so there is no way to.

    Dockscan would consider the bottles free while wijnadvies1 still promised
    them to a customer. Cancelling belongs in the advice app, which releases
    this side over the API.
    """
    sku = _bottle(db, sample_org, "HOLD-D", reserved=2)
    reservation = _reservation(db, sample_org, sku, order_id="order_open")

    resp = client.post(
        f"{BASE_URL}/{reservation.id}/release", headers=auth_header(owner_token)
    )

    assert resp.status_code in (404, 405)
    db.expire_all()
    assert db.get(AdviceReservation, reservation.id).status == "active"


def test_couriers_and_other_merchants_stay_out(
    client, db, courier_token, sample_org
):
    sku = _bottle(db, sample_org, "HOLD-E", reserved=2)
    _reservation(db, sample_org, sku, order_id="order_private")
    stranger_org = Organization(name="Andere Handel", slug="andere-handel")
    db.add(stranger_org)
    db.commit()
    stranger = User(
        username="vreemde-owner",
        email="vreemd@local",
        hashed_password=hash_password("vreemdpass"),
        role="owner",
        organization_id=stranger_org.id,
        is_verified=True,
    )
    db.add(stranger)
    db.commit()
    stranger_token = create_token(stranger.id)

    courier_list = client.get(BASE_URL, headers=auth_header(courier_token))
    stranger_list = client.get(BASE_URL, headers=auth_header(stranger_token))
    stranger_peek = client.get(
        f"{BASE_URL}?organization_id={sample_org.id}",
        headers=auth_header(stranger_token),
    )

    assert courier_list.status_code == 403
    assert stranger_list.json() == []
    assert stranger_peek.status_code == 403
