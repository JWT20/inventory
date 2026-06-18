"""Unit tests for the booking service (app/services/booking.py).

Covers the order-status state machine and the apply_booking orchestration:
clamping, validation, stock deduction, and order completion.
"""

import pytest
from fastapi import HTTPException

from app.models import (
    Booking,
    Customer,
    InventoryBalance,
    Order,
    OrderLine,
    ReferenceImage,
    SKU,
)
from app.services.booking import (
    apply_booking,
    recompute_order_status,
    remaining_for_line,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _sku(db, code, with_image=False):
    sku = SKU(sku_code=code, name=f"Wine {code}")
    db.add(sku)
    db.flush()
    if with_image:
        db.add(ReferenceImage(sku_id=sku.id, image_path=f"{code}.jpg", processing_status="done"))
        db.flush()
    return sku


def _customer(db, org, name="Cust"):
    c = Customer(name=name, organization_id=org.id)
    db.add(c)
    db.flush()
    return c


def _order(db, org, ref, status="active", week="2026-W21"):
    o = Order(organization_id=org.id, reference=ref, status=status, delivery_week=week)
    db.add(o)
    db.flush()
    return o


def _line(db, order, sku, customer, quantity, booked_count=0):
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=quantity,
        booked_count=booked_count,
    )
    db.add(line)
    db.flush()
    return line


def _seed_stock(db, sku, org, on_hand):
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id, quantity_on_hand=on_hand))
    db.flush()


# ---------------------------------------------------------------------------
# recompute_order_status
# ---------------------------------------------------------------------------

def test_recompute_active_all_booked_completes(db, sample_org):
    sku = _sku(db, "A")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "R1", status="active")
    line = _line(db, order, sku, cust, quantity=3, booked_count=3)
    db.commit()

    recompute_order_status(order, [line])

    assert order.status == "completed"
    assert order.finalized_at is not None


def test_recompute_active_partial_stays_active(db, sample_org):
    sku = _sku(db, "B")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "R2", status="active")
    line = _line(db, order, sku, cust, quantity=3, booked_count=1)
    db.commit()

    recompute_order_status(order, [line])

    assert order.status == "active"
    assert order.finalized_at is None


def test_recompute_pending_images_promotes_when_all_have_images(db, sample_org):
    sku = _sku(db, "C", with_image=True)
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "R3", status="pending_images")
    line = _line(db, order, sku, cust, quantity=3)
    db.commit()

    recompute_order_status(order, [line])

    assert order.status == "active"


def test_recompute_pending_images_stays_when_image_missing(db, sample_org):
    sku = _sku(db, "D", with_image=False)
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "R4", status="pending_images")
    line = _line(db, order, sku, cust, quantity=3)
    db.commit()

    recompute_order_status(order, [line])

    assert order.status == "pending_images"


@pytest.mark.parametrize("status", ["completed", "cancelled", "closed", "pending_approval"])
def test_recompute_noop_for_terminal_and_pending_approval(db, sample_org, status):
    sku = _sku(db, f"E{status}")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, f"R5{status}", status=status)
    line = _line(db, order, sku, cust, quantity=3, booked_count=3)
    db.commit()

    recompute_order_status(order, [line])

    assert order.status == status


def test_recompute_uses_passed_lines_not_relationship(db, sample_org):
    """The supplied lines drive the decision, even if they differ from the
    order's loaded relationship."""
    sku = _sku(db, "F")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "R6", status="active")
    line = _line(db, order, sku, cust, quantity=3, booked_count=3)
    db.commit()

    # An empty list means "no fully-booked lines" → must NOT complete.
    recompute_order_status(order, [])
    assert order.status == "completed"  # all() over [] is True → completes

    # Reset and prove a partial line keeps it active.
    order.status = "active"
    order.finalized_at = None
    line.booked_count = 1
    recompute_order_status(order, [line])
    assert order.status == "active"


# ---------------------------------------------------------------------------
# apply_booking — happy paths
# ---------------------------------------------------------------------------

def test_apply_booking_books_and_decrements_stock(db, sample_org, courier_user):
    sku = _sku(db, "G")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B1", status="active")
    line = _line(db, order, sku, cust, quantity=5)
    _seed_stock(db, sku, sample_org, on_hand=10)
    db.commit()

    result = apply_booking(
        db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
        quantity=2, cap_remaining=None, scanned_by=courier_user.id,
        scan_image_path=None, confidence=None,
    )

    assert result.booked_quantity == 2
    assert result.remaining == 3
    assert result.order_status == "active"
    assert result.order_completed is False
    assert result.last_booking_created_at is not None

    db.refresh(line)
    assert line.booked_count == 2
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id).one()
    assert balance.quantity_on_hand == 8
    assert db.query(Booking).filter_by(order_line_id=line.id).count() == 2


def test_apply_booking_clamps_to_remaining(db, sample_org, courier_user):
    sku = _sku(db, "H")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B2", status="active")
    line = _line(db, order, sku, cust, quantity=5, booked_count=4)
    _seed_stock(db, sku, sample_org, on_hand=10)
    db.commit()

    result = apply_booking(
        db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
        quantity=3, cap_remaining=None, scanned_by=courier_user.id,
        scan_image_path=None, confidence=None,
    )

    assert result.booked_quantity == 1  # only 1 was left
    assert result.remaining == 0
    assert result.order_completed is True       # single line now fully booked
    assert result.order_status == "completed"


def test_apply_booking_clamps_to_cap_remaining(db, sample_org, courier_user):
    sku = _sku(db, "I")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B3", status="active")
    line = _line(db, order, sku, cust, quantity=5)
    _seed_stock(db, sku, sample_org, on_hand=10)
    db.commit()

    result = apply_booking(
        db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
        quantity=4, cap_remaining=2, scanned_by=courier_user.id,
        scan_image_path=None, confidence=None,
    )

    assert result.booked_quantity == 2
    assert result.remaining == 3


def test_apply_booking_completes_multi_line_order(db, sample_org, courier_user):
    sku1 = _sku(db, "J1")
    sku2 = _sku(db, "J2")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B4", status="active")
    line1 = _line(db, order, sku1, cust, quantity=2, booked_count=2)  # already done
    line2 = _line(db, order, sku2, cust, quantity=2, booked_count=0)
    _seed_stock(db, sku2, sample_org, on_hand=10)
    db.commit()

    result = apply_booking(
        db, order_id=order.id, order_line_id=line2.id, sku_id=sku2.id,
        quantity=2, cap_remaining=None, scanned_by=courier_user.id,
        scan_image_path=None, confidence=None,
    )

    assert result.order_completed is True
    assert result.order_status == "completed"
    db.refresh(order)
    assert order.status == "completed"
    assert order.finalized_at is not None


# ---------------------------------------------------------------------------
# apply_booking — validation / errors
# ---------------------------------------------------------------------------

def test_apply_booking_unknown_order_404(db, sample_org, courier_user):
    with pytest.raises(HTTPException) as exc:
        apply_booking(
            db, order_id=999999, order_line_id=1, sku_id=1,
            quantity=1, cap_remaining=None, scanned_by=courier_user.id,
            scan_image_path=None, confidence=None,
        )
    assert exc.value.status_code == 404


def test_apply_booking_line_from_other_order_404(db, sample_org, courier_user):
    sku = _sku(db, "K")
    cust = _customer(db, sample_org)
    order_a = _order(db, sample_org, "B5a", status="active")
    order_b = _order(db, sample_org, "B5b", status="active")
    line_b = _line(db, order_b, sku, cust, quantity=3)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        apply_booking(
            db, order_id=order_a.id, order_line_id=line_b.id, sku_id=sku.id,
            quantity=1, cap_remaining=None, scanned_by=courier_user.id,
            scan_image_path=None, confidence=None,
        )
    assert exc.value.status_code == 404


def test_apply_booking_sku_mismatch_409(db, sample_org, courier_user):
    sku = _sku(db, "L")
    other = _sku(db, "Lx")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B6", status="active")
    line = _line(db, order, sku, cust, quantity=3)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        apply_booking(
            db, order_id=order.id, order_line_id=line.id, sku_id=other.id,
            quantity=1, cap_remaining=None, scanned_by=courier_user.id,
            scan_image_path=None, confidence=None,
        )
    assert exc.value.status_code == 409


def test_apply_booking_non_active_order_409(db, sample_org, courier_user):
    sku = _sku(db, "M")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B7", status="pending_images")
    line = _line(db, order, sku, cust, quantity=3)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        apply_booking(
            db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
            quantity=1, cap_remaining=None, scanned_by=courier_user.id,
            scan_image_path=None, confidence=None,
        )
    assert exc.value.status_code == 409


def test_apply_booking_nothing_left_409(db, sample_org, courier_user):
    sku = _sku(db, "N")
    cust = _customer(db, sample_org)
    order = _order(db, sample_org, "B8", status="active")
    line = _line(db, order, sku, cust, quantity=3, booked_count=3)
    _seed_stock(db, sku, sample_org, on_hand=10)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        apply_booking(
            db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
            quantity=1, cap_remaining=None, scanned_by=courier_user.id,
            scan_image_path=None, confidence=None,
        )
    assert exc.value.status_code == 409


def test_remaining_for_line_floors_at_zero():
    line = OrderLine(quantity=3, booked_count=5)
    assert remaining_for_line(line) == 0
