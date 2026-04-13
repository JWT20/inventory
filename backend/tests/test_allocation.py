"""Tests for the weekly allocation algorithm."""

import pytest

from app.models import Customer, InventoryBalance, Order, OrderLine, SKU
from app.services.allocation import compute_allocation


@pytest.fixture
def org(db, sample_org):
    return sample_org


@pytest.fixture
def sku(db):
    s = SKU(sku_code="WIJN-001", name="Test Wijn")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_customer(db, org, name):
    c = Customer(name=name, organization_id=org.id)
    db.add(c)
    db.flush()
    return c


def _make_order_line(db, org, sku, customer, quantity, booked_count=0,
                     week="2026-W16", delivery_day="wednesday"):
    """Helper: create an active order with a single line and return the line."""
    order = Order(
        organization_id=org.id,
        reference=f"ORD-{customer.name}-{id(customer)}-{quantity}",
        status="active",
        delivery_week=week,
    )
    db.add(order)
    db.flush()

    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=quantity,
        booked_count=booked_count,
        delivery_day=delivery_day,
    )
    db.add(line)
    db.flush()
    return line


def _set_stock(db, sku, org, qty):
    bal = InventoryBalance(sku_id=sku.id, organization_id=org.id, quantity_on_hand=qty)
    db.add(bal)
    db.flush()


class TestComputeAllocation:
    """Test the greedy smallest-first allocation."""

    def test_user_example(self, db, org, sku):
        """Stock=10, A=2, B=4, C=8 → A=2, B=4, C=4."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")
        cC = _make_customer(db, org, "C")
        lA = _make_order_line(db, org, sku, cA, 2)
        lB = _make_order_line(db, org, sku, cB, 4)
        lC = _make_order_line(db, org, sku, cC, 8)
        _set_stock(db, sku, org, 10)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        assert caps[lA.id] == 2  # complete
        assert caps[lB.id] == 4  # complete
        assert caps[lC.id] == 4  # partial

    def test_enough_stock(self, db, org, sku):
        """Stock=20, A=2, B=4, C=8 → everyone gets everything."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")
        cC = _make_customer(db, org, "C")
        lA = _make_order_line(db, org, sku, cA, 2)
        lB = _make_order_line(db, org, sku, cB, 4)
        lC = _make_order_line(db, org, sku, cC, 8)
        _set_stock(db, sku, org, 20)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        assert caps[lA.id] == 2
        assert caps[lB.id] == 4
        assert caps[lC.id] == 8

    def test_extreme_scarcity(self, db, org, sku):
        """Stock=2, 5 orders of 2 → first 2 get 1 each, rest get 0."""
        customers = [_make_customer(db, org, f"C{i}") for i in range(5)]
        lines = [_make_order_line(db, org, sku, c, 2) for c in customers]
        _set_stock(db, sku, org, 2)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        # 2 lines get booked_count+1 = 1, 3 lines stay at booked_count = 0
        got_one = sum(1 for l in lines if caps[l.id] == 1)
        got_zero = sum(1 for l in lines if caps[l.id] == 0)
        assert got_one == 2
        assert got_zero == 3

    def test_scarcity_just_enough(self, db, org, sku):
        """Stock=5, 5 orders of 2 → each gets 1 reserved, then fill smallest first."""
        customers = [_make_customer(db, org, f"C{i}") for i in range(5)]
        lines = [_make_order_line(db, org, sku, c, 2) for c in customers]
        _set_stock(db, sku, org, 5)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        # 5 stock, 5 lines → reserve 1 each → pool = 0. Everyone gets exactly 1.
        for l in lines:
            assert caps[l.id] == 1

    def test_no_active_orders(self, db, org, sku):
        """No orders → empty dict."""
        _set_stock(db, sku, org, 10)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        assert caps == {}

    def test_partially_booked(self, db, org, sku):
        """Stock=5, A ordered 4 already booked 2 → cap considers remaining."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")
        lA = _make_order_line(db, org, sku, cA, 4, booked_count=2)
        lB = _make_order_line(db, org, sku, cB, 3)
        _set_stock(db, sku, org, 5)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        # A remaining=2, B remaining=3, total needed=5, stock=5 → enough
        assert caps[lA.id] == 4  # 2 booked + 2 remaining = full
        assert caps[lB.id] == 3  # full

    def test_same_customer_two_orders(self, db, org, sku):
        """Same customer, different orders → lines treated separately."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")

        # A has 2 orders (different delivery days would cause this, but here same day)
        lA1 = _make_order_line(db, org, sku, cA, 3)
        lA2 = _make_order_line(db, org, sku, cA, 2)
        lB = _make_order_line(db, org, sku, cB, 4)
        _set_stock(db, sku, org, 6)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        # Sorted by remaining: A2(2), A1(3), B(4). Total=9, stock=6.
        # Reserve 1 each → pool=3.
        # A2: extra=min(1,3)=1 → cap=2 (complete), pool=2
        # A1: extra=min(2,2)=2 → cap=3 (complete), pool=0
        # B: extra=min(3,0)=0 → cap=1 (partial)
        assert caps[lA2.id] == 2
        assert caps[lA1.id] == 3
        assert caps[lB.id] == 1

    def test_week_filtering(self, db, org, sku):
        """Only orders in the requested week are considered."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")

        lA = _make_order_line(db, org, sku, cA, 3, week="2026-W16")
        _make_order_line(db, org, sku, cB, 5, week="2026-W17")  # different week
        _set_stock(db, sku, org, 3)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        # Only A's line should be present (W16), B is W17
        assert lA.id in caps
        assert len(caps) == 1
        assert caps[lA.id] == 3  # enough stock for this line alone

    def test_day_filtering(self, db, org, sku):
        """Only orders on the requested delivery day are considered."""
        cA = _make_customer(db, org, "A")
        cB = _make_customer(db, org, "B")

        lA = _make_order_line(db, org, sku, cA, 3, delivery_day="wednesday")
        _make_order_line(db, org, sku, cB, 5, delivery_day="thursday")
        _set_stock(db, sku, org, 3)

        caps = compute_allocation(db, "2026-W16", sku.id, org.id, "wednesday")

        assert lA.id in caps
        assert len(caps) == 1
        assert caps[lA.id] == 3
