from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_product_manager
from app.database import get_db
from app.events import publish_event
from app.models import Order, OrderLine, SKU, User
from app.schemas import (
    OrderCreate,
    OrderLineResponse,
    OrderResponse,
    OrderUpdate,
)

router = APIRouter(
    prefix="/orders", tags=["orders"], dependencies=[Depends(get_current_user)]
)

VALID_STATUSES = ("pending", "receiving", "fulfilled")


def _line_to_response(line: OrderLine) -> OrderLineResponse:
    return OrderLineResponse(
        id=line.id,
        sku_id=line.sku_id,
        sku_code=line.sku.sku_code,
        sku_name=line.sku.name,
        quantity=line.quantity,
        received_quantity=line.received_quantity,
        status=line.status,
    )


def _order_to_response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        customer_name=order.customer_name,
        dock_location=order.dock_location,
        status=order.status,
        created_at=order.created_at,
        updated_at=order.updated_at,
        lines=[_line_to_response(l) for l in order.lines],
    )


@router.get("", response_model=list[OrderResponse])
def list_orders(status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.created_at.desc()).all()
    return [_order_to_response(o) for o in orders]


@router.post("", response_model=OrderResponse, status_code=201)
def create_order(
    data: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    existing = db.query(Order).filter(Order.order_number == data.order_number).first()
    if existing:
        raise HTTPException(400, f"Order '{data.order_number}' already exists")

    order = Order(
        order_number=data.order_number,
        customer_name=data.customer_name,
        dock_location=data.dock_location,
    )
    db.add(order)
    db.flush()

    for line_data in data.lines:
        sku = db.query(SKU).filter(SKU.sku_code == line_data.sku_code).first()
        if not sku:
            raise HTTPException(404, f"SKU '{line_data.sku_code}' not found")
        line = OrderLine(
            order_id=order.id,
            sku_id=sku.id,
            quantity=line_data.quantity,
        )
        db.add(line)

    db.commit()
    db.refresh(order)
    publish_event(
        "order_created",
        details={
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "dock_location": order.dock_location,
            "line_count": len(order.lines),
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )
    return _order_to_response(order)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    return _order_to_response(order)


@router.patch("/{order_id}", response_model=OrderResponse)
def update_order(
    order_id: int,
    data: OrderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")

    if data.customer_name is not None:
        order.customer_name = data.customer_name
    if data.dock_location is not None:
        order.dock_location = data.dock_location

    db.commit()
    db.refresh(order)
    return _order_to_response(order)


@router.patch("/{order_id}/status")
def update_order_status(
    order_id: int,
    status: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")
    old_status = order.status
    order.status = status
    db.commit()
    publish_event(
        "order_status_changed",
        details={
            "order_number": order.order_number,
            "old_status": old_status,
            "new_status": status,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )
    return {"status": order.status}


@router.delete("/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    order_number = order.order_number
    db.delete(order)
    db.commit()
    publish_event(
        "order_deleted",
        details={"order_number": order_number},
        user=user,
        resource_type="order",
        resource_id=order_id,
    )
