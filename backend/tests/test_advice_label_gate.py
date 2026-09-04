"""The shipping-label gate on an image-picked order that ships in boxes."""

import datetime

import pytest

from app.auth import create_token, hash_password
from app.models import (
    CarrierConnection,
    Order,
    OrderLine,
    OrderParcel,
    Organization,
    ReferenceImage,
    SKU,
    User,
)
from app.services.channel_credentials import store_carrier_api_key
from app.services.veloyd import VeloydLabel, VeloydLabelMismatch
from tests.conftest import auth_header


def _wine_org(db, slug="wijn-labelgate"):
    """A merchant that picks bottles by image and ships them by carrier."""
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "vision_picking"]
    db.add(org)
    db.flush()
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, f"{slug}-veloyd-key")
    db.commit()
    return org


def _courier(db, username="labelgate-courier"):
    user = User(
        username=username,
        email=f"{username}@local",
        hashed_password=hash_password("CourierPass1!"),
        role="courier",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    return create_token(user.id)


def _advice_order(db, org, *, reference="JUR-2026-8DP6QY", boxes=1, status="completed"):
    sku = SKU(
        sku_code=f"WIJN-{reference}",
        name="Wijn",
        organization_id=org.id,
        product_type="vision",
        is_bottle=True,
        source_product_id=f"prd_{reference.lower()}",
    )
    db.add(sku)
    db.flush()
    db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
    order = Order(
        organization_id=org.id,
        channel="advice",
        external_id=f"ext-{reference}",
        reference=f"ADV-{reference[-6:]}",
        channel_reference=reference,
        status=status,
        inventory_location="webshop",
    )
    db.add(order)
    db.flush()
    db.add(OrderLine(order_id=order.id, sku_id=sku.id, quantity=6 * boxes))
    for sequence in range(1, boxes + 1):
        db.add(
            OrderParcel(
                order_id=order.id,
                sequence=sequence,
                veloyd_parcel_id=f"veloyd-{reference}-{sequence}",
            )
        )
    db.commit()
    db.refresh(order)
    return order


def _open_by_label(client, token, label):
    return client.post(
        "/api/picking/open-by-label",
        json={"label_reference": label},
        headers=auth_header(token),
    )


def _scan_label(client, token, order_id, label):
    return client.post(
        "/api/picking/scan-label",
        json={"order_id": order_id, "label_reference": label},
        headers=auth_header(token),
    )


def _veloyd_returns(monkeypatch, *, parcel_id, tracking, reference):
    def _lookup(_self, _scanned):
        return VeloydLabel(
            reference=reference, tracking_number=tracking, parcel_id=parcel_id
        )

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _lookup
    )


def test_an_image_picked_order_may_pass_the_label_gate(db, client, monkeypatch):
    """The label is a barcode; what is in the box was identified another way."""
    org = _wine_org(db)
    order = _advice_order(db, org)
    token = _courier(db)
    _veloyd_returns(
        monkeypatch,
        parcel_id="veloyd-JUR-2026-8DP6QY-1",
        tracking="3SIJVT018280390",
        reference="JUR-2026-8DP6QY",
    )

    resp = _open_by_label(client, token, "3SIJVT018280390")

    assert resp.status_code == 200
    assert resp.json()["order_id"] == order.id
    assert resp.json()["parcel_sequence"] == 1
    assert resp.json()["parcel_count"] == 1


def test_the_box_is_recognised_by_its_veloyd_id(db, client, monkeypatch):
    """The order number is on every box; only the parcel id says which one."""
    org = _wine_org(db)
    order = _advice_order(db, org, boxes=3)
    token = _courier(db)
    _veloyd_returns(
        monkeypatch,
        parcel_id="veloyd-JUR-2026-8DP6QY-2",
        tracking="3SIJVT018280391",
        reference="JUR-2026-8DP6QY",
    )

    resp = _open_by_label(client, token, "3SIJVT018280391")

    assert resp.json()["parcel_sequence"] == 2
    assert resp.json()["parcel_count"] == 3
    parcel = (
        db.query(OrderParcel)
        .filter(OrderParcel.veloyd_parcel_id == "veloyd-JUR-2026-8DP6QY-2")
        .one()
    )
    assert parcel.tracking_code == "3sijvt018280391"


def test_a_code_the_webhook_already_reported_needs_no_veloyd_call(
    db, client, monkeypatch
):
    org = _wine_org(db)
    order = _advice_order(db, org)
    parcel = order.parcels[0]
    parcel.tracking_code = "3sijvt018280390"
    db.commit()
    token = _courier(db)

    def _must_not_ask(_self, _scanned):
        raise AssertionError("Veloyd was asked about a code we already knew")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _must_not_ask
    )

    resp = _open_by_label(client, token, "3SIJVT018280390")

    assert resp.status_code == 200
    assert resp.json()["order_id"] == order.id


def test_an_order_ships_only_when_every_box_is_scanned(db, client, monkeypatch):
    """A case of twelve must not travel with one label read and one not."""
    org = _wine_org(db)
    order = _advice_order(db, org, boxes=2)
    for index, parcel in enumerate(order.parcels, start=1):
        parcel.tracking_code = f"3sijvt01828039{index}"
    db.commit()
    token = _courier(db)

    first = _scan_label(client, token, order.id, "3SIJVT018280391")

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert first.json()["parcels_scanned"] == 1
    assert first.json()["parcels_total"] == 2
    db.refresh(order)
    assert order.status == "completed"

    second = _scan_label(client, token, order.id, "3SIJVT018280392")

    assert second.json()["status"] == "shipped"
    assert second.json()["parcels_scanned"] == 2
    db.refresh(order)
    assert order.status == "shipped"


def test_scanning_the_same_box_twice_does_not_ship_the_order(db, client, monkeypatch):
    org = _wine_org(db)
    order = _advice_order(db, org, boxes=2)
    for index, parcel in enumerate(order.parcels, start=1):
        parcel.tracking_code = f"3sijvt01828039{index}"
    db.commit()
    token = _courier(db)

    _scan_label(client, token, order.id, "3SIJVT018280391")
    again = _scan_label(client, token, order.id, "3SIJVT018280391")

    assert again.status_code == 200
    assert again.json()["status"] == "completed"
    assert again.json()["parcels_scanned"] == 1
    db.refresh(order)
    assert order.status == "completed"


def test_the_order_number_alone_does_not_ship_a_boxed_order(
    db, client, monkeypatch
):
    """It is printed on every box, so it cannot prove any of them was seen."""
    org = _wine_org(db)
    order = _advice_order(db, org, boxes=2)
    token = _courier(db)

    def _unknown(_self, _scanned):
        raise VeloydLabelMismatch("Label is niet bekend bij Veloyd")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _unknown
    )

    resp = _scan_label(client, token, order.id, "JUR-2026-8DP6QY")

    assert resp.status_code == 409
    db.refresh(order)
    assert order.status == "completed"


def test_a_label_of_another_order_is_refused(db, client, monkeypatch):
    org = _wine_org(db)
    order = _advice_order(db, org, boxes=1)
    other = _advice_order(db, org, reference="JUR-2026-OTHER1", boxes=1)
    token = _courier(db)
    _veloyd_returns(
        monkeypatch,
        parcel_id="veloyd-JUR-2026-OTHER1-1",
        tracking="3SIJVT018280399",
        reference="JUR-2026-OTHER1",
    )

    resp = _scan_label(client, token, order.id, "3SIJVT018280399")

    assert resp.status_code == 409
    db.refresh(order)
    assert order.status == "completed"
    assert other.parcels[0].scanned_at is None


def test_an_order_with_mixed_picking_methods_is_refused(db, client, monkeypatch):
    """Two methods on one order have no single module to check against."""
    org = _wine_org(db, "wijn-mixed")
    order = _advice_order(db, org, reference="JUR-2026-MIXED1")
    barcode_sku = SKU(
        sku_code="SOK-MIXED",
        name="Sok",
        organization_id=org.id,
        product_type="barcode",
        ean="8700000009999",
    )
    db.add(barcode_sku)
    db.flush()
    db.add(OrderLine(order_id=order.id, sku_id=barcode_sku.id, quantity=1))
    db.commit()
    token = _courier(db)

    resp = _scan_label(client, token, order.id, "3SIJVT018280390")

    assert resp.status_code == 409
    assert "pickmethode" in resp.json()["detail"]


def test_a_merchant_without_the_picking_module_is_still_refused(
    db, client, monkeypatch
):
    """Loosening the gate must not turn it into no gate at all."""
    org = _wine_org(db, "wijn-no-module")
    org.modules = ["inventory", "orders"]
    db.commit()
    order = _advice_order(db, org, reference="JUR-2026-NOMOD1")
    parcel = order.parcels[0]
    parcel.tracking_code = "3sijvt018280390"
    db.commit()
    owner = User(
        username="owner-no-module",
        email="owner-no-module@local",
        hashed_password=hash_password("OwnerPass1!"),
        role="owner",
        organization_id=org.id,
        is_verified=True,
    )
    db.add(owner)
    db.commit()

    resp = _scan_label(client, create_token(owner.id), order.id, "3SIJVT018280390")

    assert resp.status_code == 403
