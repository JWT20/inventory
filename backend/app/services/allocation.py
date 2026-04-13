"""Weekly allocation: cap-aware stock distribution across order lines.

Given a SKU, a delivery week, and a delivery day, determine the maximum
number of boxes each order line may receive so that:
  - As many orders as possible are completed
  - No customer ends up with 0 (unless mathematically impossible)

Algorithm: greedy smallest-first.
"""

from sqlalchemy.orm import Session

from app.models import InventoryBalance, Order, OrderLine


def compute_allocation(
    db: Session,
    week: str,
    sku_id: int,
    organization_id: int | None,
    delivery_day: str,
) -> dict[int, int]:
    """Return {order_line_id: max_total_booked_count} for the given SKU on the given day.

    Parameters
    ----------
    db : Session
        Active SQLAlchemy session.
    week : str
        ISO week string, e.g. "2026-W16".
    sku_id : int
        The SKU to allocate.
    organization_id : int | None
        Org scope for inventory balance.
    delivery_day : str
        "wednesday", "thursday", or "friday".
    """
    # 1. Fetch all active order lines for this SKU + week + day
    lines = (
        db.query(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .filter(
            Order.delivery_week == week,
            Order.status == "active",
            OrderLine.sku_id == sku_id,
            OrderLine.delivery_day == delivery_day,
        )
    )
    if organization_id is not None:
        lines = lines.filter(Order.organization_id == organization_id)
    lines = lines.all()

    # 2. Compute remaining per line, discard fully booked
    remaining_map: dict[int, tuple[OrderLine, int]] = {}
    for line in lines:
        remaining = line.quantity - line.booked_count
        if remaining > 0:
            remaining_map[line.id] = (line, remaining)

    if not remaining_map:
        return {}

    # 3. Get current stock
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .first()
    )
    available = balance.quantity_on_hand if balance else 0

    if available <= 0:
        return {line_id: line.booked_count for line_id, (line, _rem) in remaining_map.items()}

    # 4. Sort by remaining ASC (smallest orders first)
    sorted_lines = sorted(remaining_map.items(), key=lambda item: item[1][1])

    total_needed = sum(rem for _, (_, rem) in sorted_lines)

    # 5. Enough for everyone → return full quantities
    if available >= total_needed:
        return {line_id: line.quantity for line_id, (line, _rem) in sorted_lines}

    # 6. Extreme scarcity: fewer boxes than lines → first N get 1 each
    n_lines = len(sorted_lines)
    if available <= n_lines:
        caps: dict[int, int] = {}
        given = 0
        for line_id, (line, _rem) in sorted_lines:
            if given < available:
                caps[line_id] = line.booked_count + 1
                given += 1
            else:
                caps[line_id] = line.booked_count  # no additional boxes
        return caps

    # 7. Normal scarcity: reserve 1 per line, then fill smallest first
    pool = available - n_lines
    caps = {}
    for line_id, (line, rem) in sorted_lines:
        extra = min(rem - 1, pool)
        caps[line_id] = line.booked_count + 1 + extra
        pool -= extra

    return caps
