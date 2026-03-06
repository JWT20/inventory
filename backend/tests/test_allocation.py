"""Tests for cross-docking allocation service."""

import pytest

from app.models import Order, OrderLine, SKU
from app.services.allocation import confirm_receipt, find_allocation


class TestFindAllocation:
    def test_finds_open_order(self, db, sample_order, sample_sku):
        result = find_allocation(db, sample_sku.id)
        assert result is not None
        line, order = result
        assert order.order_number == "ORD-001"
        assert line.quantity == 3
        assert line.received_quantity == 0

    def test_no_allocation_when_no_orders(self, db, sample_sku):
        result = find_allocation(db, sample_sku.id)
        assert result is None

    def test_no_allocation_for_wrong_sku(self, db, sample_order, second_sku):
        result = find_allocation(db, second_sku.id)
        assert result is None

    def test_fifo_oldest_order_first(self, db, sample_sku):
        """When multiple orders need the same SKU, oldest gets priority."""
        order1 = Order(
            order_number="ORD-OLD",
            customer_name="First",
            dock_location="C1",
        )
        db.add(order1)
        db.flush()
        db.add(OrderLine(order_id=order1.id, sku_id=sample_sku.id, quantity=2))

        order2 = Order(
            order_number="ORD-NEW",
            customer_name="Second",
            dock_location="C2",
        )
        db.add(order2)
        db.flush()
        db.add(OrderLine(order_id=order2.id, sku_id=sample_sku.id, quantity=2))
        db.commit()

        result = find_allocation(db, sample_sku.id)
        assert result is not None
        _, order = result
        assert order.order_number == "ORD-OLD"

    def test_skips_fulfilled_orders(self, db, sample_sku):
        order = Order(
            order_number="ORD-DONE",
            customer_name="Done",
            status="fulfilled",
        )
        db.add(order)
        db.flush()
        db.add(OrderLine(
            order_id=order.id, sku_id=sample_sku.id,
            quantity=2, received_quantity=2, status="fulfilled",
        ))
        db.commit()

        result = find_allocation(db, sample_sku.id)
        assert result is None

    def test_skips_fully_received_lines(self, db, sample_sku):
        order = Order(
            order_number="ORD-PARTIAL",
            customer_name="Partial",
            dock_location="C1",
            status="receiving",
        )
        db.add(order)
        db.flush()
        db.add(OrderLine(
            order_id=order.id, sku_id=sample_sku.id,
            quantity=2, received_quantity=2, status="fulfilled",
        ))
        db.commit()

        result = find_allocation(db, sample_sku.id)
        assert result is None


class TestConfirmReceipt:
    def test_increments_received_quantity(self, db, sample_order):
        line_id = sample_order.lines[0].id
        line, order = confirm_receipt(db, line_id)
        assert line.received_quantity == 1
        assert line.status == "partial"
        assert order.status == "receiving"

    def test_fulfills_line_when_complete(self, db, sample_sku):
        order = Order(
            order_number="ORD-ALMOST",
            customer_name="Almost",
            dock_location="C1",
            status="receiving",
        )
        db.add(order)
        db.flush()
        line = OrderLine(
            order_id=order.id, sku_id=sample_sku.id,
            quantity=1, received_quantity=0,
        )
        db.add(line)
        db.commit()

        line, order = confirm_receipt(db, line.id)
        assert line.status == "fulfilled"
        assert line.received_quantity == 1
        assert order.status == "fulfilled"

    def test_fulfills_order_when_all_lines_done(self, db, sample_sku, second_sku):
        order = Order(
            order_number="ORD-MULTI",
            customer_name="Multi",
            dock_location="C1",
            status="receiving",
        )
        db.add(order)
        db.flush()
        line1 = OrderLine(
            order_id=order.id, sku_id=sample_sku.id,
            quantity=1, received_quantity=0,
        )
        line2 = OrderLine(
            order_id=order.id, sku_id=second_sku.id,
            quantity=1, received_quantity=1, status="fulfilled",
        )
        db.add_all([line1, line2])
        db.commit()

        line1, order = confirm_receipt(db, line1.id)
        assert line1.status == "fulfilled"
        assert order.status == "fulfilled"

    def test_does_not_exceed_quantity(self, db, sample_sku):
        order = Order(
            order_number="ORD-OVER",
            customer_name="Over",
            dock_location="C1",
        )
        db.add(order)
        db.flush()
        line = OrderLine(
            order_id=order.id, sku_id=sample_sku.id,
            quantity=1, received_quantity=1, status="fulfilled",
        )
        db.add(line)
        db.commit()

        line, _ = confirm_receipt(db, line.id)
        assert line.received_quantity == 1  # capped at quantity

    def test_nonexistent_line_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            confirm_receipt(db, 9999)
