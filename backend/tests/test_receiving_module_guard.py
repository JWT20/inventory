"""Tests for the per-order module guard on the vision receiving flow (Fase 1).

``assert_order_module`` keys the picking method off the *order's* organization,
not the caller's — so the courier flow (couriers have no org of their own) is
gated by whichever merchant the order belongs to. The photo/AI receiving flow
must only run for orders whose organization has the ``vision_picking`` module.
"""
import pytest
from fastapi import HTTPException

from app.auth import assert_order_module, create_token, hash_password
from app.models import Customer, InventoryBalance, Order, OrderLine, Organization, SKU, User
from tests.conftest import auth_header


# --- helpers ---------------------------------------------------------------

def _make_org(db, slug, modules):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_order(db, org, ref="ORD", status="active", week="2026-W21"):
    order = Order(
        organization_id=org.id if org else None,
        reference=ref,
        status=status,
        delivery_week=week,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# --- unit: assert_order_module --------------------------------------------

class TestAssertOrderModule:
    def test_passes_when_order_org_has_module(self, db, courier_user):
        org = _make_org(db, "vision-org", ["inventory", "orders", "vision_picking"])
        order = _make_order(db, org)
        # Does not raise.
        assert_order_module(order, "vision_picking", courier_user)

    def test_rejects_when_order_org_lacks_module(self, db, courier_user):
        org = _make_org(db, "barcode-org", ["inventory", "orders", "barcode_picking"])
        order = _make_order(db, org)
        with pytest.raises(HTTPException) as exc:
            assert_order_module(order, "vision_picking", courier_user)
        assert exc.value.status_code == 403

    def test_platform_admin_bypasses(self, db, admin_user):
        org = _make_org(db, "barcode-org2", ["inventory", "orders", "barcode_picking"])
        order = _make_order(db, org)
        # Admin manages every org, so the guard never blocks them.
        assert_order_module(order, "vision_picking", admin_user)

    def test_orgless_order_is_rejected(self, db, courier_user):
        order = _make_order(db, None)
        with pytest.raises(HTTPException) as exc:
            assert_order_module(order, "vision_picking", courier_user)
        assert exc.value.status_code == 403


# --- integration: the vision flow is gated on the order's org -------------

def _seed_distribution(db, org):
    sku = SKU(sku_code="SKU-G", name="Wine G")
    db.add(sku)
    db.flush()
    customer = Customer(name="Alpha", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = _make_order(db, org, ref=f"DIST-{org.slug}")
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=5,
        booked_count=0,
        delivery_day="wednesday",
    )
    db.add(line)
    db.add(InventoryBalance(
        sku_id=sku.id, organization_id=org.id, quantity_on_hand=10, quantity_reserved=0,
    ))
    db.commit()
    return order, sku


def test_distribution_blocked_for_barcode_only_org(client, db, courier_token):
    org = _make_org(db, "socks", ["inventory", "orders", "barcode_picking"])
    order, sku = _seed_distribution(db, org)

    resp = client.get(
        f"/api/receiving/distribution?order_id={order.id}&sku_id={sku.id}",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403


def test_distribution_allowed_for_vision_org(client, db, courier_token):
    org = _make_org(db, "wine", ["inventory", "orders", "vision_picking"])
    order, sku = _seed_distribution(db, org)

    resp = client.get(
        f"/api/receiving/distribution?order_id={order.id}&sku_id={sku.id}",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 200
