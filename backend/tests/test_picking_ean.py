"""Tests for the barcode/EAN picking endpoint (Fase 1, PR-C).

POST /api/picking/scan-ean books one unit on a barcode order by scanned EAN.
It is gated on the order's barcode_picking module (keyed on the order's org,
not the courier's) and resolves the EAN within that org.
"""
import pytest

from app.auth import create_token, hash_password
from app.models import (
    CarrierConnection,
    Customer,
    InventoryBalance,
    Order,
    OrderLine,
    Organization,
    SKU,
    User,
)
from app.services.channel_credentials import store_carrier_api_key
from app.services.fulfillment_sync import ShopifyFulfillmentError
from app.services.veloyd import VeloydError, VeloydLabel, VeloydLabelMismatch
from tests.conftest import auth_header


@pytest.fixture(autouse=True)
def _stub_shopify_fulfillment(monkeypatch):
    """Keep endpoint tests offline; service/client behavior has focused tests."""

    def _fulfill(_db, order, *, tracking_info=None):
        order.channel_fulfillment_status = "fulfilled"
        return True

    monkeypatch.setattr("app.routers.picking.fulfill_shopify_order", _fulfill)


def _make_org(db, slug, modules):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_barcode_sku(db, org, code, ean):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _make_active_order(db, org, sku, quantity=2, booked=0):
    customer = Customer(name="Kanaalklant", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(organization_id=org.id, reference=f"EAN-{sku.sku_code}",
                  status="active", channel="shopify", external_id=f"X-{sku.sku_code}")
    db.add(order)
    db.flush()
    line = OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                     klant=customer.name, quantity=quantity, booked_count=booked,
                     delivery_day="wednesday")
    db.add(line)
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    db.commit()
    db.refresh(order)
    db.refresh(line)
    return order, line


def _scan(client, token, order_id, ean):
    return client.post(
        "/api/picking/scan-ean",
        json={"order_id": order_id, "ean": ean},
        headers=auth_header(token),
    )


def _barcode_org(db, slug="socks-pick"):
    org = _make_org(
        db, slug, ["inventory", "orders", "barcode_picking", "channel_orders"]
    )
    # A merchant that ships has its own account at the carrier; without one the
    # label flow refuses to reach Veloyd at all.
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, f"{slug}-veloyd-key")
    db.commit()
    return org


def _make_owner_token(db, org, username):
    owner = User(
        username=username, email=f"{username}@local",
        hashed_password=hash_password("OwnerPass1!"), role="owner",
        organization_id=org.id, is_verified=True,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return create_token(owner.id)


def _open_by_label(client, token, label):
    return client.post(
        "/api/picking/open-by-label",
        json={"label_reference": label},
        headers=auth_header(token),
    )


def test_scan_books_one_unit(client, db, courier_token):
    org = _barcode_org(db)
    sku = _make_barcode_sku(db, org, "SOK-1", "8711111111111")
    order, line = _make_active_order(db, org, sku, quantity=2)

    resp = _scan(client, courier_token, order.id, "8711111111111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sku_code"] == "SOK-1"
    assert data["booked_quantity"] == 1
    assert data["remaining_quantity"] == 1
    assert data["order_completed"] is False

    db.refresh(line)
    assert line.booked_count == 1


def test_scan_completes_order_on_last_unit(client, db, courier_token):
    org = _barcode_org(db, "socks-done")
    sku = _make_barcode_sku(db, org, "SOK-2", "8722222222222")
    order, _line = _make_active_order(db, org, sku, quantity=1)

    resp = _scan(client, courier_token, order.id, "8722222222222")
    assert resp.status_code == 200
    assert resp.json()["order_completed"] is True


def test_unknown_ean_returns_404(client, db, courier_token):
    org = _barcode_org(db, "socks-unknown")
    sku = _make_barcode_sku(db, org, "SOK-3", "8733333333333")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "0000000000000")
    assert resp.status_code == 404


def test_ean_from_other_org_not_found(client, db, courier_token):
    """An EAN that exists, but in another organization, must not resolve here."""
    org = _barcode_org(db, "socks-a")
    other = _barcode_org(db, "socks-b")
    sku = _make_barcode_sku(db, org, "SOK-4", "8744444444444")
    # Same EAN string, different org — legitimate per uq_skus_org_ean.
    _make_barcode_sku(db, other, "SOK-4B", "8744444444444")
    order, _ = _make_active_order(db, org, sku)

    # The order is in `org`; scanning resolves within org and finds SOK-4. Now
    # an order in `other` scanning its own product is fine — but a product that
    # only exists in `other` must be invisible to an `org` order:
    foreign = _make_barcode_sku(db, other, "ONLY-B", "8755555555555")
    resp = _scan(client, courier_token, order.id, "8755555555555")
    assert resp.status_code == 404


def test_product_not_on_order_returns_400(client, db, courier_token):
    org = _barcode_org(db, "socks-noton")
    sku = _make_barcode_sku(db, org, "SOK-5", "8766666666666")
    other_sku = _make_barcode_sku(db, org, "SOK-5B", "8777777777777")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "8777777777777")
    assert resp.status_code == 400  # exists in org, but not on this order


def test_already_complete_returns_409(client, db, courier_token):
    org = _barcode_org(db, "socks-complete")
    sku = _make_barcode_sku(db, org, "SOK-6", "8788888888888")
    order, _ = _make_active_order(db, org, sku, quantity=1, booked=1)

    resp = _scan(client, courier_token, order.id, "8788888888888")
    assert resp.status_code == 409


def test_blocked_when_org_lacks_barcode_module(client, db, courier_token):
    """A vision org's order must not be EAN-pickable, even if a product has an EAN."""
    org = _make_org(db, "wine-novbarcode", ["inventory", "orders", "vision_picking"])
    sku = _make_barcode_sku(db, org, "WINE-EAN", "8799999999999")
    order, _ = _make_active_order(db, org, sku)

    resp = _scan(client, courier_token, order.id, "8799999999999")
    assert resp.status_code == 403


def test_barcode_order_exposes_pick_method_and_surfaces_weekless(client, db, courier_token):
    """A weekless born-active barcode order is pick_method='barcode' and shows
    in the courier list even when a week is selected (it has no week)."""
    org = _barcode_org(db, "socks-list")
    sku = _make_barcode_sku(db, org, "SOK-8", "8700000000002")
    order, _ = _make_active_order(db, org, sku)  # weekless, active

    resp = client.get("/api/orders?week=2026-W21", headers=auth_header(courier_token))
    assert resp.status_code == 200
    by_id = {o["id"]: o for o in resp.json()}
    assert order.id in by_id
    assert by_id[order.id]["pick_method"] == "barcode"


def test_owner_cannot_book_other_orgs_order(client, db):
    """An owner of org A must not be able to book against org B's order, even
    though both orgs have barcode_picking (module check != access check)."""
    org_b = _barcode_org(db, "socks-b-owned")
    sku = _make_barcode_sku(db, org_b, "SOK-OB", "8700000000200")
    order, _ = _make_active_order(db, org_b, sku)
    # Owner belongs to a different barcode org.
    org_a = _barcode_org(db, "socks-a-owned")
    token = _make_owner_token(db, org_a, "owner-a")

    resp = _scan(client, token, order.id, "8700000000200")
    assert resp.status_code == 403


def test_owner_can_book_own_orgs_order(client, db):
    org = _barcode_org(db, "socks-own")
    sku = _make_barcode_sku(db, org, "SOK-OWN", "8700000000201")
    order, _ = _make_active_order(db, org, sku)
    token = _make_owner_token(db, org, "owner-self")

    resp = _scan(client, token, order.id, "8700000000201")
    assert resp.status_code == 200


def test_inactive_order_rejected(client, db, courier_token):
    org = _barcode_org(db, "socks-inactive")
    sku = _make_barcode_sku(db, org, "SOK-7", "8700000000001")
    order, _ = _make_active_order(db, org, sku)
    order.status = "pending_approval"
    db.commit()

    resp = _scan(client, courier_token, order.id, "8700000000001")
    assert resp.status_code == 400


# --- Undo (PR1) -----------------------------------------------------------


def _undo(client, token, booking_id):
    return client.post(
        "/api/picking/undo",
        json={"booking_id": booking_id},
        headers=auth_header(token),
    )


def test_undo_reverses_one_unit_and_restocks(client, db, courier_token):
    org = _barcode_org(db, "socks-undo")
    sku = _make_barcode_sku(db, org, "SOK-U1", "8700000001001")
    order, line = _make_active_order(db, org, sku, quantity=2)

    booking_id = _scan(client, courier_token, order.id, "8700000001001").json()["booking_id"]
    balance = (
        db.query(InventoryBalance)
        .filter_by(sku_id=sku.id, organization_id=org.id)
        .one()
    )
    db.refresh(balance)
    assert balance.quantity_on_hand == 9  # 10 - 1 picked

    resp = _undo(client, courier_token, booking_id)
    assert resp.status_code == 200
    assert resp.json()["remaining_quantity"] == 2

    db.refresh(line)
    db.refresh(balance)
    assert line.booked_count == 0
    assert balance.quantity_on_hand == 10  # restocked


def test_undo_reopens_completed_order(client, db, courier_token):
    org = _barcode_org(db, "socks-undo-done")
    sku = _make_barcode_sku(db, org, "SOK-U2", "8700000001002")
    order, _line = _make_active_order(db, org, sku, quantity=1)

    booking_id = _scan(client, courier_token, order.id, "8700000001002").json()["booking_id"]
    db.refresh(order)
    assert order.status == "completed"
    assert order.finalized_at is not None

    resp = _undo(client, courier_token, booking_id)
    assert resp.status_code == 200
    assert resp.json()["order_status"] == "active"

    db.refresh(order)
    assert order.status == "active"
    assert order.finalized_at is None


def test_undo_blocked_after_shipped(client, db, courier_token):
    org = _barcode_org(db, "socks-undo-shipped")
    sku = _make_barcode_sku(db, org, "SOK-U3", "8700000001003")
    order, _ = _make_active_order(db, org, sku, quantity=1)
    order.channel_reference = "1262"
    db.commit()

    booking_id = _scan(client, courier_token, order.id, "8700000001003").json()["booking_id"]
    ship = _scan_label(client, courier_token, order.id, "1262")
    assert ship.status_code == 200

    resp = _undo(client, courier_token, booking_id)
    assert resp.status_code == 409


# --- Open order by loose Veloyd label -------------------------------------


def test_loose_label_opens_order_and_persists_tracking_code(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-open-label")
    sku = _make_barcode_sku(db, org, "SOK-OL", "8700000001901")
    order, _ = _make_active_order(db, org, sku)
    order.channel_reference = "1262"
    db.commit()

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number",
        lambda _self, scanned: VeloydLabel(
            reference="1262", tracking_number="V793AUDS9F4MB"
        ),
    )

    resp = _open_by_label(client, courier_token, "V793AUDS9F4MB")

    assert resp.status_code == 200
    assert resp.json() == {
        "order_id": order.id,
        "tracking_code": "v793auds9f4mb",
        "parcel_sequence": None,
        "parcel_count": None,
    }
    db.refresh(order)
    assert order.veloyd_tracking_code == "v793auds9f4mb"


def test_known_loose_label_opens_locally_without_veloyd(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-open-known")
    sku = _make_barcode_sku(db, org, "SOK-OK", "8700000001902")
    order, _ = _make_active_order(db, org, sku)
    order.channel_reference = "1263"
    order.veloyd_tracking_code = "vknown"
    db.commit()

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("known tracking code must not call Veloyd")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _unexpected
    )

    resp = _open_by_label(client, courier_token, "VKNOWN")
    assert resp.status_code == 200
    assert resp.json()["order_id"] == order.id


def test_channel_reference_barcode_opens_order_without_veloyd(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-open-reference")
    sku = _make_barcode_sku(db, org, "SOK-OR", "8700000001906")
    order, _ = _make_active_order(db, org, sku)
    order.channel_reference = "1280"
    db.commit()

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("channel reference must resolve without Veloyd")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _unexpected
    )

    resp = _open_by_label(client, courier_token, "#1280")

    assert resp.status_code == 200
    assert resp.json() == {
        "order_id": order.id,
        "tracking_code": "1280",
        "parcel_sequence": None,
        "parcel_count": None,
    }
    db.refresh(order)
    assert order.veloyd_tracking_code is None


def test_channel_reference_barcode_blocks_ambiguous_orders(
    client, db, courier_token, monkeypatch
):
    org_a = _barcode_org(db, "socks-open-reference-duplicate-a")
    org_b = _barcode_org(db, "socks-open-reference-duplicate-b")
    sku_a = _make_barcode_sku(db, org_a, "SOK-ORA", "8700000001907")
    sku_b = _make_barcode_sku(db, org_b, "SOK-ORB", "8700000001908")
    order_a, _ = _make_active_order(db, org_a, sku_a)
    order_b, _ = _make_active_order(db, org_b, sku_b)
    order_a.channel_reference = "1281"
    order_b.channel_reference = "1281"
    db.commit()

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("ambiguous reference must not call Veloyd")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _unexpected
    )

    resp = _open_by_label(client, courier_token, "1281")

    assert resp.status_code == 409
    assert "Meerdere open orders" in resp.json()["detail"]


def test_unknown_loose_veloyd_label_returns_404(
    client, db, courier_token, monkeypatch
):
    # A connected merchant to ask; without one the scan fails on configuration
    # rather than on the label.
    _barcode_org(db, "socks-unknown-label")

    def _unknown(_self, _scanned):
        raise VeloydLabelMismatch("Label hoort bij een andere order")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _unknown
    )

    resp = _open_by_label(client, courier_token, "VUNKNOWN")
    assert resp.status_code == 404
    assert "Geen order gevonden" in resp.json()["detail"]


def test_loose_label_blocks_ambiguous_channel_order_number(
    client, db, courier_token, monkeypatch
):
    org_a = _barcode_org(db, "socks-open-duplicate-a")
    org_b = _barcode_org(db, "socks-open-duplicate-b")
    sku_a = _make_barcode_sku(db, org_a, "SOK-ODA", "8700000001903")
    sku_b = _make_barcode_sku(db, org_b, "SOK-ODB", "8700000001904")
    order_a, _ = _make_active_order(db, org_a, sku_a)
    order_b, _ = _make_active_order(db, org_b, sku_b)
    order_a.channel_reference = "1264"
    order_b.channel_reference = "1264"
    db.commit()

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number",
        lambda _self, scanned: VeloydLabel(
            reference="1264", tracking_number="VAMBIGUOUS"
        ),
    )

    resp = _open_by_label(client, courier_token, "VAMBIGUOUS")
    assert resp.status_code == 409
    assert "Meerdere open orders" in resp.json()["detail"]
    db.refresh(order_a)
    db.refresh(order_b)
    assert order_a.veloyd_tracking_code is None
    assert order_b.veloyd_tracking_code is None


def test_known_label_does_not_open_inactive_order(client, db, courier_token):
    org = _barcode_org(db, "socks-open-inactive")
    sku = _make_barcode_sku(db, org, "SOK-OI", "8700000001905")
    order, _ = _make_active_order(db, org, sku)
    order.channel_reference = "1265"
    order.veloyd_tracking_code = "vinactive"
    order.status = "shipped"
    db.commit()

    resp = _open_by_label(client, courier_token, "VINACTIVE")
    assert resp.status_code == 409


# --- Label verification gate (PR1) ----------------------------------------


def _scan_label(client, token, order_id, label):
    return client.post(
        "/api/picking/scan-label",
        json={"order_id": order_id, "label_reference": label},
        headers=auth_header(token),
    )


def _complete_order(db, org, code, ean, ref):
    sku = _make_barcode_sku(db, org, code, ean)
    order, _ = _make_active_order(db, org, sku, quantity=1, booked=1)
    order.status = "completed"
    order.channel_reference = ref
    db.commit()
    db.refresh(order)
    return order


def test_label_match_ships_order(client, db, courier_token):
    org = _barcode_org(db, "socks-lbl-ok")
    order = _complete_order(db, org, "SOK-L1", "8700000002001", "1262")

    resp = _scan_label(client, courier_token, order.id, "1262")
    assert resp.status_code == 200
    assert resp.json()["status"] == "shipped"

    db.refresh(order)
    assert order.status == "shipped"
    assert order.channel_fulfillment_status == "fulfilled"


def test_label_match_marks_shopify_before_local_ship(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-shopify-fail")
    order = _complete_order(db, org, "SOK-LSF", "8700000002010", "1272")

    def _fail(_db, _order, *, tracking_info=None):
        raise ShopifyFulfillmentError(
            "Shopify kon de order niet als verzonden markeren; probeer opnieuw"
        )

    monkeypatch.setattr("app.routers.picking.fulfill_shopify_order", _fail)
    resp = _scan_label(client, courier_token, order.id, "1272")

    assert resp.status_code == 502
    assert "Shopify kon" in resp.json()["detail"]
    db.refresh(order)
    assert order.status == "completed"
    assert order.channel_fulfillment_status is None


def test_bol_label_scan_leaves_external_shipment_to_veloyd(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-bol")
    order = _complete_order(db, org, "SOK-LBOL", "8700000002018", "BOL-1272")
    order.channel = "bol"
    db.commit()

    def _must_not_fulfill(*_args, **_kwargs):
        raise AssertionError("Bol shipment confirmation belongs to Veloyd")

    monkeypatch.setattr("app.routers.picking.fulfill_shopify_order", _must_not_fulfill)
    response = _scan_label(client, courier_token, order.id, "BOL-1272")

    assert response.status_code == 200
    db.refresh(order)
    assert order.status == "shipped"
    assert order.channel_fulfillment_status is None


def test_veloyd_tracking_barcode_is_resolved_before_shopify_fulfillment(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-veloyd")
    order = _complete_order(db, org, "SOK-LV", "8700000002012", "1262")
    seen = {}

    class ResolvedLabel:
        tracking_number = "V793AUDS9F4MB"
        shopify_tracking_info = {
            "number": "V793AUDS9F4MB",
            "url": "https://tracking.example/V793AUDS9F4MB",
            "company": "Break Away",
        }

    def _verify(scanned, expected, *, client=None):
        seen["verify"] = (scanned, expected)
        return ResolvedLabel()

    def _fulfill(_db, target_order, *, tracking_info=None):
        seen["fulfill"] = (target_order.id, tracking_info)
        target_order.channel_fulfillment_status = "fulfilled"
        return True

    monkeypatch.setattr("app.routers.picking.verify_veloyd_label", _verify)
    monkeypatch.setattr("app.routers.picking.fulfill_shopify_order", _fulfill)

    resp = _scan_label(client, courier_token, order.id, "V793AUDS9F4MB")

    assert resp.status_code == 200
    assert seen["verify"] == ("V793AUDS9F4MB", "1262")
    assert seen["fulfill"][1]["number"] == "V793AUDS9F4MB"


def test_loose_label_full_flow_accepts_same_barcode_with_different_formatting(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-full-flow")
    sku = _make_barcode_sku(db, org, "SOK-LFF", "8700000002014")
    order, _ = _make_active_order(db, org, sku, quantity=1)
    order.channel_reference = "1267"
    db.commit()

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number",
        lambda _self, scanned: VeloydLabel(
            reference="1267", tracking_number="V-ABC-123"
        ),
    )
    opened = _open_by_label(client, courier_token, "vabc123")
    assert opened.status_code == 200
    db.refresh(order)
    assert order.veloyd_tracking_code == "vabc123"

    picked = _scan(client, courier_token, order.id, "8700000002014")
    assert picked.status_code == 200
    assert picked.json()["order_completed"] is True

    monkeypatch.setattr(
        "app.routers.picking.verify_veloyd_label",
        lambda scanned, expected, *, client=None: VeloydLabel(
            reference=expected, tracking_number="V-ABC-123"
        ),
    )
    shipped = _scan_label(client, courier_token, order.id, "V-ABC-123")
    assert shipped.status_code == 200
    assert shipped.json()["status"] == "shipped"


def test_final_scan_backfills_normalized_tracking_code(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-backfill")
    order = _complete_order(db, org, "SOK-LBF", "8700000002015", "1268")

    monkeypatch.setattr(
        "app.routers.picking.verify_veloyd_label",
        lambda scanned, expected, *, client=None: VeloydLabel(
            reference=expected, tracking_number="V-BACK-FILL"
        ),
    )

    resp = _scan_label(client, courier_token, order.id, "V-BACK-FILL")
    assert resp.status_code == 200
    db.refresh(order)
    assert order.veloyd_tracking_code == "vbackfill"


def test_final_scan_tracking_conflict_blocks_before_shopify(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-backfill-conflict")
    target = _complete_order(db, org, "SOK-LBC1", "8700000002016", "1269")
    other = _complete_order(db, org, "SOK-LBC2", "8700000002017", "1270")
    other.veloyd_tracking_code = "vconflict"
    db.commit()

    monkeypatch.setattr(
        "app.routers.picking.verify_veloyd_label",
        lambda scanned, expected, *, client=None: VeloydLabel(
            reference=expected, tracking_number="V-CONFLICT"
        ),
    )

    def _must_not_fulfill(*_args, **_kwargs):
        raise AssertionError("Shopify must not be called after a tracking conflict")

    monkeypatch.setattr("app.routers.picking.fulfill_shopify_order", _must_not_fulfill)

    resp = _scan_label(client, courier_token, target.id, "V-CONFLICT")
    assert resp.status_code == 409
    db.refresh(target)
    assert target.status == "completed"
    assert target.veloyd_tracking_code is None


def test_final_scan_requires_same_physical_label(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-same-physical")
    order = _complete_order(db, org, "SOK-LSP", "8700000002013", "1266")
    order.veloyd_tracking_code = "vright"
    db.commit()

    resp = _scan_label(client, courier_token, order.id, "VWRONG")

    assert resp.status_code == 409
    assert "hetzelfde Veloyd-label" in resp.json()["detail"]
    db.refresh(order)
    assert order.status == "completed"


def test_platform_admin_can_ship_cross_org_order(
    client, db, admin_token
):
    org = _barcode_org(db, "socks-lbl-platform-admin")
    order = _complete_order(db, org, "SOK-LPA", "8700000002011", "1273")

    resp = _scan_label(client, admin_token, order.id, "1273")

    assert resp.status_code == 200
    db.refresh(order)
    assert order.status == "shipped"


def test_label_match_strips_leading_hash(client, db, courier_token):
    org = _barcode_org(db, "socks-lbl-hash")
    order = _complete_order(db, org, "SOK-L2", "8700000002002", "1263")

    resp = _scan_label(client, courier_token, order.id, "#1263")
    assert resp.status_code == 200
    assert resp.json()["status"] == "shipped"


def test_label_mismatch_blocks(client, db, courier_token, monkeypatch):
    org = _barcode_org(db, "socks-lbl-bad")
    order = _complete_order(db, org, "SOK-L3", "8700000002003", "1262")

    def _mismatch(_scanned, _expected, *, client=None):
        from app.services.veloyd import VeloydLabelMismatch

        raise VeloydLabelMismatch("Label hoort bij een andere order")

    monkeypatch.setattr("app.routers.picking.verify_veloyd_label", _mismatch)

    resp = _scan_label(client, courier_token, order.id, "9999")
    assert resp.status_code == 409

    db.refresh(order)
    assert order.status == "completed"  # unchanged


def test_label_blocked_when_not_complete(client, db, courier_token):
    org = _barcode_org(db, "socks-lbl-active")
    sku = _make_barcode_sku(db, org, "SOK-L4", "8700000002004")
    order, _ = _make_active_order(db, org, sku, quantity=2, booked=1)
    order.channel_reference = "1262"
    db.commit()

    resp = _scan_label(client, courier_token, order.id, "1262")
    assert resp.status_code == 400


def test_label_blocked_when_already_shipped(client, db, courier_token):
    org = _barcode_org(db, "socks-lbl-twice")
    order = _complete_order(db, org, "SOK-L5", "8700000002005", "1262")

    assert _scan_label(client, courier_token, order.id, "1262").status_code == 200
    resp = _scan_label(client, courier_token, order.id, "1262")
    assert resp.status_code == 409


def test_label_blocked_without_channel_reference(client, db, courier_token):
    org = _barcode_org(db, "socks-lbl-noref")
    sku = _make_barcode_sku(db, org, "SOK-L6", "8700000002006")
    order, _ = _make_active_order(db, org, sku, quantity=1, booked=1)
    order.status = "completed"
    db.commit()

    resp = _scan_label(client, courier_token, order.id, "1262")
    assert resp.status_code == 409


def test_shipped_order_in_courier_history_not_worklist(client, db, courier_token):
    """A shipped order drops out of the active worklist but stays visible under
    include_history (PR2: shipped consistency)."""
    org = _barcode_org(db, "socks-shipped-list")
    order = _complete_order(db, org, "SOK-LH", "8700000003001", "1262")
    assert _scan_label(client, courier_token, order.id, "1262").status_code == 200

    worklist = client.get("/api/orders", headers=auth_header(courier_token)).json()
    assert order.id not in {o["id"] for o in worklist}

    history = client.get(
        "/api/orders?include_history=true", headers=auth_header(courier_token)
    ).json()
    by_id = {o["id"]: o for o in history}
    assert order.id in by_id
    assert by_id[order.id]["status"] == "shipped"


def test_a_courier_label_is_resolved_against_the_second_merchant(
    client, db, courier_token, monkeypatch
):
    """A courier rides for several merchants; the first account may not know it."""
    _barcode_org(db, "socks-first-merchant")
    second = _barcode_org(db, "socks-second-merchant")
    order = _complete_order(db, second, "SOK-TWO", "8700000002101", "2201")
    asked: list[str] = []

    def _by_account(self, scanned):
        asked.append(self.api_key)
        if self.api_key == "socks-first-merchant-veloyd-key":
            raise VeloydLabelMismatch("Label is niet bekend bij Veloyd")
        return VeloydLabel(reference="2201", tracking_number="V-SECOND-1")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _by_account
    )

    resp = _open_by_label(client, courier_token, "V-SECOND-1")

    assert resp.status_code == 200
    assert resp.json()["order_id"] == order.id
    assert asked == [
        "socks-first-merchant-veloyd-key",
        "socks-second-merchant-veloyd-key",
    ]


def test_an_unreachable_account_is_not_reported_as_an_unknown_label(
    client, db, courier_token, monkeypatch
):
    """"Not found" would send the courier looking for a label that is fine."""
    _barcode_org(db, "socks-outage")

    def _down(_self, _scanned):
        raise VeloydError("Veloyd is tijdelijk niet bereikbaar")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.parcel_by_tracking_number", _down
    )

    resp = _open_by_label(client, courier_token, "V-OUTAGE")

    assert resp.status_code == 502


def test_a_merchant_without_a_carrier_account_cannot_scan_a_label(
    client, db, monkeypatch
):
    org = _make_org(
        db, "socks-no-carrier", ["inventory", "orders", "barcode_picking", "channel_orders"]
    )
    token = _make_owner_token(db, org, "owner-no-carrier")

    resp = _open_by_label(client, token, "V-NO-ACCOUNT")

    assert resp.status_code == 409
