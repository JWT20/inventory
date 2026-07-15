"""Tests for the barcode/EAN picking endpoint (Fase 1, PR-C).

POST /api/picking/scan-ean books one unit on a barcode order by scanned EAN.
It is gated on the order's barcode_picking module (keyed on the order's org,
not the courier's) and resolves the EAN within that org.
"""
import pytest

from app.auth import create_token, hash_password
from app.models import Customer, InventoryBalance, Order, OrderLine, Organization, SKU, User
from app.services.fulfillment_sync import ShopifyFulfillmentError
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
    return _make_org(db, slug, ["inventory", "orders", "barcode_picking", "channel_orders"])


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


def test_veloyd_tracking_barcode_is_resolved_before_shopify_fulfillment(
    client, db, courier_token, monkeypatch
):
    org = _barcode_org(db, "socks-lbl-veloyd")
    order = _complete_order(db, org, "SOK-LV", "8700000002012", "1262")
    seen = {}

    class ResolvedLabel:
        shopify_tracking_info = {
            "number": "V793AUDS9F4MB",
            "url": "https://tracking.example/V793AUDS9F4MB",
            "company": "Break Away",
        }

    def _verify(scanned, expected):
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

    def _mismatch(_scanned, _expected):
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
