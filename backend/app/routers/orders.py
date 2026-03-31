"""Order management: manual order creation and lifecycle."""

import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin, require_can_create_orders
from app.database import get_db
from app.events import publish_event
from app.models import Customer, CustomerSKU, Order, OrderLine, SKU, User
from app.schemas import (
    BookingResponse,
    ManualOrderCreate,
    OrderLineResponse,
    OrderResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


def _calc_effective_price(
    unit_price: float | None,
    discount_type: str | None,
    discount_value: float | None,
    default_price: float | None,
) -> float | None:
    if unit_price is not None:
        return unit_price
    if default_price is not None and discount_type and discount_value is not None:
        if discount_type == "percentage":
            return round(default_price * (1 - discount_value / 100), 2)
        if discount_type == "fixed":
            return round(max(0, default_price - discount_value), 2)
    return default_price


def _as_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _order_line_to_response(
    line: OrderLine,
    sku_default_prices: dict[int, float | None],
    customer_price_map: dict[tuple[int, int], CustomerSKU],
) -> OrderLineResponse:
    customer_show_prices = True
    if line.customer is not None:
        customer_show_prices = line.customer.show_prices

    link = None
    if line.customer_id is not None:
        link = customer_price_map.get((line.customer_id, line.sku_id))

    unit_price = _as_float(link.unit_price) if link else None
    discount_type = link.discount_type if link else None
    discount_value = _as_float(link.discount_value) if link else None
    effective_price = _calc_effective_price(
        unit_price,
        discount_type,
        discount_value,
        sku_default_prices.get(line.sku_id),
    )
    line_total = (
        round(effective_price * line.quantity, 2)
        if customer_show_prices and effective_price is not None
        else None
    )

    return OrderLineResponse(
        id=line.id,
        sku_id=line.sku_id,
        sku_code=line.sku.sku_code,
        sku_name=line.sku.name,
        klant=line.klant,
        customer_id=line.customer_id,
        customer_name=line.customer_name,
        quantity=line.quantity,
        booked_count=line.booked_count,
        has_image=len(line.sku.reference_images) > 0,
        show_prices=customer_show_prices,
        unit_price=unit_price if customer_show_prices else None,
        discount_type=discount_type if customer_show_prices else None,
        discount_value=discount_value if customer_show_prices else None,
        effective_price=effective_price if customer_show_prices else None,
        line_total=line_total,
    )


def _order_to_response(order: Order, db: Session) -> OrderResponse:
    customer_sku_keys = {
        (line.customer_id, line.sku_id)
        for line in order.lines
        if line.customer_id is not None
    }
    customer_price_map: dict[tuple[int, int], CustomerSKU] = {}
    if customer_sku_keys:
        customer_ids = sorted({customer_id for customer_id, _ in customer_sku_keys})
        sku_ids = sorted({sku_id for _, sku_id in customer_sku_keys})
        links = (
            db.query(CustomerSKU)
            .filter(
                CustomerSKU.customer_id.in_(customer_ids),
                CustomerSKU.sku_id.in_(sku_ids),
            )
            .all()
        )
        customer_price_map = {
            (link.customer_id, link.sku_id): link
            for link in links
            if (link.customer_id, link.sku_id) in customer_sku_keys
        }

    sku_default_prices = {
        line.sku_id: _as_float(line.sku.default_price)
        for line in order.lines
    }
    lines = [
        _order_line_to_response(line, sku_default_prices, customer_price_map)
        for line in order.lines
    ]
    visible_line_totals = [line.line_total for line in lines if line.line_total is not None]
    visible_total = round(sum(visible_line_totals), 2) if visible_line_totals else None
    hidden_lines_count = len([line for line in lines if not line.show_prices])

    return OrderResponse(
        id=order.id,
        reference=order.reference,
        status=order.status,
        organization_name=order.organization.name if order.organization else "",
        created_by_name=order.creator.username if order.creator else "",
        created_at=order.created_at,
        updated_at=order.updated_at,
        lines=lines,
        total_boxes=sum(l.quantity for l in order.lines),
        booked_boxes=sum(l.booked_count for l in order.lines),
        visible_total=visible_total,
        hidden_lines_count=hidden_lines_count,
    )


def _upsert_customer_skus(db: Session, pairs: set[tuple[int, int]]):
    """Ensure customer_skus rows exist for the given (customer_id, sku_id) pairs."""
    for customer_id, sku_id in pairs:
        exists = (
            db.query(CustomerSKU)
            .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
            .first()
        )
        if not exists:
            db.add(CustomerSKU(customer_id=customer_id, sku_id=sku_id))


def _resolve_organization_id(user: User, body_org_id: int | None, db: Session) -> int:
    """Determine the organization_id for an order based on user context."""
    if user.is_platform_admin:
        if body_org_id:
            return body_org_id
        raise HTTPException(400, "Platform admin must specify organization_id")
    if user.organization_id:
        return user.organization_id
    raise HTTPException(400, "User has no organization")


@router.post("", response_model=OrderResponse)
def create_order(
    body: ManualOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Create an order by picking existing customers and SKUs."""
    org_id = _resolve_organization_id(user, body.organization_id, db)

    # Customer-role users can only order for their linked customer
    if user.role == "customer" and user.customer_id:
        for line in body.lines:
            if line.customer_id != user.customer_id:
                raise HTTPException(
                    403,
                    "Klantgebruikers kunnen alleen orders plaatsen voor hun eigen klant",
                )

    ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = Order(
        organization_id=org_id,
        created_by=user.id,
        reference=ref,
        status="draft",
    )
    db.add(order)
    db.flush()

    # Group by (customer_id, sku_id), sum quantities
    line_quantities: dict[tuple[int, int], int] = {}
    for line in body.lines:
        key = (line.customer_id, line.sku_id)
        line_quantities[key] = line_quantities.get(key, 0) + line.quantity

    sku_cache: dict[int, SKU] = {}
    customer_sku_pairs: set[tuple[int, int]] = set()

    for (customer_id, sku_id), qty in line_quantities.items():
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404, f"Klant met id {customer_id} niet gevonden")
        sku = sku_cache.get(sku_id) or db.get(SKU, sku_id)
        if not sku:
            raise HTTPException(404, f"SKU met id {sku_id} niet gevonden")
        sku_cache[sku_id] = sku

        db.add(OrderLine(
            order_id=order.id,
            sku_id=sku_id,
            customer_id=customer_id,
            klant=customer.name,
            quantity=qty,
        ))
        customer_sku_pairs.add((customer_id, sku_id))

    # Auto-populate customer_skus catalog
    _upsert_customer_skus(db, customer_sku_pairs)

    # Determine status
    all_have_images = all(
        len(s.reference_images) > 0 for s in sku_cache.values()
    )
    order.status = "active" if all_have_images else "pending_images"

    db.commit()
    db.refresh(order)

    publish_event(
        "order_created_manual",
        details={"order_reference": ref, "total_lines": len(line_quantities)},
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db)


@router.get("", response_model=list[OrderResponse])
def list_orders(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List orders based on user role.

    - Platform admin: all orders
    - Org owner/member: orders for their organization
    - Customer: only their own orders
    - Courier: all active orders (for delivery)
    """
    query = db.query(Order)

    if user.is_platform_admin:
        pass  # See everything
    elif user.role == "courier":
        query = query.filter(Order.status == "active")
    elif user.role == "customer":
        query = query.filter(Order.created_by == user.id)
    elif user.organization_id:
        query = query.filter(Order.organization_id == user.organization_id)
    else:
        return []

    orders = query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()
    return [_order_to_response(o, db) for o in orders]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    # Access control
    if not user.is_platform_admin:
        if user.role == "customer" and order.created_by != user.id:
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.role == "courier" and order.status != "active":
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.organization_id and order.organization_id != user.organization_id:
            if user.role != "courier":
                raise HTTPException(403, "Geen toegang tot deze order")

    return _order_to_response(order, db)


@router.post("/{order_id}/activate", response_model=OrderResponse)
def activate_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Activate order — only possible when all SKUs have reference images."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    # Only platform admin or org owner can activate
    if not user.is_platform_admin and user.role != "owner":
        raise HTTPException(403, "Alleen eigenaren kunnen orders activeren")
    if not user.is_platform_admin and order.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang tot deze order")

    if order.status not in ("draft", "pending_images"):
        raise HTTPException(400, f"Order kan niet geactiveerd worden (status: {order.status})")

    # Check all SKUs have images
    skus_without_images = []
    for line in order.lines:
        if len(line.sku.reference_images) == 0:
            skus_without_images.append(line.sku.sku_code)

    if skus_without_images:
        raise HTTPException(
            400,
            f"Niet alle SKU's hebben referentiebeelden: {', '.join(skus_without_images)}",
        )

    order.status = "active"
    db.commit()
    db.refresh(order)

    publish_event(
        "order_activated",
        details={"order_reference": order.reference},
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db)


@router.delete("/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Delete an order and all its lines and bookings (platform admin only)."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    reference = order.reference
    db.delete(order)
    db.commit()

    publish_event(
        "order_deleted",
        details={"order_reference": reference},
        user=user,
        resource_type="order",
        resource_id=order_id,
    )


@router.get("/{order_id}/bookings", response_model=list[BookingResponse])
def list_bookings(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models import Booking

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    # Access control: same rules as get_order
    if not user.is_platform_admin:
        if user.role == "customer" and order.created_by != user.id:
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.role == "courier" and order.status != "active":
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.organization_id and order.organization_id != user.organization_id:
            if user.role != "courier":
                raise HTTPException(403, "Geen toegang tot deze order")

    bookings = (
        db.query(Booking)
        .filter(Booking.order_id == order_id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return [
        BookingResponse(
            id=b.id,
            order_id=b.order_id,
            order_reference=order.reference,
            sku_code=b.sku.sku_code,
            sku_name=b.sku.name,
            klant=b.order_line.customer_name,
            rolcontainer=f"KLANT {b.order_line.customer_name.upper()}",
            created_at=b.created_at,
        )
        for b in bookings
    ]
