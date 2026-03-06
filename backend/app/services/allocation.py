"""Cross-docking allocation service.

Finds the best order to allocate an incoming box to (FIFO),
and handles confirming receipt against order lines.
"""

from sqlalchemy.orm import Session

from app.models import Order, OrderLine


def find_allocation(db: Session, sku_id: int) -> tuple[OrderLine, Order] | None:
    """Find the oldest open order line that needs this SKU (FIFO)."""
    line = (
        db.query(OrderLine)
        .join(Order)
        .filter(
            OrderLine.sku_id == sku_id,
            OrderLine.received_quantity < OrderLine.quantity,
            Order.status.in_(["pending", "receiving"]),
        )
        .order_by(Order.created_at.asc())
        .first()
    )
    if not line:
        return None
    return line, line.order


def confirm_receipt(db: Session, line_id: int) -> tuple[OrderLine, Order]:
    """Increment received_quantity and update statuses."""
    line = db.get(OrderLine, line_id)
    if not line:
        raise ValueError(f"OrderLine {line_id} not found")

    line.received_quantity = min(line.received_quantity + 1, line.quantity)
    if line.received_quantity >= line.quantity:
        line.status = "fulfilled"
    elif line.received_quantity > 0:
        line.status = "partial"

    order = line.order
    if order.status == "pending":
        order.status = "receiving"
    if all(l.status == "fulfilled" for l in order.lines):
        order.status = "fulfilled"

    db.commit()
    return line, order
