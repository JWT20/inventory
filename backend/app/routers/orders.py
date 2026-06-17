"""Order management: manual order creation and lifecycle."""

import datetime
import logging
import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_can_create_orders, require_merchant
from app.database import get_db
from app.events import publish_event
from app.models import (
    Customer,
    CustomerSKU,
    Order,
    OrderLine,
    Organization,
    SKU,
    Supplier,
    User,
)
from app.schemas import (
    BookingResponse,
    ManualOrderCreate,
    NextPickResponse,
    OrderApprove,
    MonthlyBoxesMonth,
    MonthlyBoxesOrganization,
    MonthlyBoxesResponse,
    OrderLineAdd,
    OrderLineDeleteResponse,
    OrderLineResponse,
    OrderLineUpdate,
    OrderResponse,
    OrderUpdate,
    WeeklyPickPhotoResponse,
    WeeklySummaryCustomer,
    WeeklySummaryCustomerLine,
    WeeklySummaryCustomerOrder,
    WeeklySummaryResponse,
    WeeklySummarySupplier,
    WeeklySummaryWine,
)
from app.services.pricing import calc_effective_price

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])

# Statuses a courier may view (matches the courier order list). Orders
# awaiting merchant approval are visible read-only, so the courier can see
# what work may be coming.
COURIER_VIEWABLE_STATUSES = (
    "pending_approval", "active", "completed", "cancelled", "closed",
)


def _as_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _order_line_to_response(
    line: OrderLine,
    sku_default_prices: dict[int, float | None],
    customer_price_map: dict[tuple[int, int], CustomerSKU],
    hide_prices: bool = False,
) -> OrderLineResponse:
    customer_show_prices = True
    if line.customer is not None:
        customer_show_prices = line.customer.show_prices
    # Couriers are delivery staff, not part of the sales relationship; they
    # never see prices regardless of the customer's show_prices setting.
    if hide_prices:
        customer_show_prices = False

    link = None
    if line.customer_id is not None:
        link = customer_price_map.get((line.customer_id, line.sku_id))

    unit_price = _as_float(link.unit_price) if link else None
    discount_type = link.discount_type if link else None
    discount_value = _as_float(link.discount_value) if link else None
    customer_discount = (
        _as_float(line.customer.discount_percentage)
        if line.customer is not None
        else None
    )
    effective_price = calc_effective_price(
        sku_default_prices.get(line.sku_id),
        unit_price,
        discount_type,
        discount_value,
        customer_discount,
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
        delivery_day=line.delivery_day,
        quantity=line.quantity,
        booked_count=line.booked_count,
        has_image=len(line.sku.reference_images) > 0,
        is_bottle=line.sku.is_bottle,
        show_prices=customer_show_prices,
        unit_price=unit_price if customer_show_prices else None,
        discount_type=discount_type if customer_show_prices else None,
        discount_value=discount_value if customer_show_prices else None,
        effective_price=effective_price if customer_show_prices else None,
        line_total=line_total,
    )


def _order_to_response(
    order: Order, db: Session, hide_prices: bool = False
) -> OrderResponse:
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
        _order_line_to_response(
            line, sku_default_prices, customer_price_map, hide_prices=hide_prices
        )
        for line in order.lines
    ]
    visible_line_totals = [line.line_total for line in lines if line.line_total is not None]
    visible_total = round(sum(visible_line_totals), 2) if visible_line_totals else None
    hidden_lines_count = len([line for line in lines if not line.show_prices])

    return OrderResponse(
        id=order.id,
        reference=order.reference,
        status=order.status,
        remarks=order.remarks or "",
        delivery_week=order.delivery_week,
        organization_id=order.organization_id,
        organization_name=order.organization.name if order.organization else "",
        created_by_name=order.creator.username if order.creator else "",
        created_at=order.created_at,
        updated_at=order.updated_at,
        customer_name=order.lines[0].customer_name if order.lines else None,
        lines=lines,
        total_boxes=sum(l.quantity for l in order.lines if not l.sku.is_bottle),
        booked_boxes=sum(l.booked_count for l in order.lines if not l.sku.is_bottle),
        total_bottles=sum(l.quantity for l in order.lines if l.sku.is_bottle),
        booked_bottles=sum(l.booked_count for l in order.lines if l.sku.is_bottle),
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


def _customer_assigned_sku_ids(db: Session, customer_id: int) -> set[int]:
    """Return the set of sku_ids assigned to the given customer."""
    rows = (
        db.query(CustomerSKU.sku_id)
        .filter(CustomerSKU.customer_id == customer_id)
        .all()
    )
    return {r[0] for r in rows}


def _customer_can_view_order(user: User, order: Order) -> bool:
    """A customer-role user may view an order they created themselves
    or any order with at least one line linked to their customer_id."""
    if user.role != "customer":
        return True
    if order.created_by == user.id:
        return True
    if user.customer_id and any(l.customer_id == user.customer_id for l in order.lines):
        return True
    return False


def _resolve_organization_id(user: User, body_org_id: int | None, db: Session) -> int:
    """Determine the organization_id for an order based on user context."""
    if user.is_platform_admin:
        if body_org_id:
            return body_org_id
        raise HTTPException(400, "Platform admin must specify organization_id")
    if user.organization_id:
        return user.organization_id
    raise HTTPException(400, "User has no organization")


def _check_delivery_day_allowed(delivery_day: str | None, customer: Customer) -> None:
    """Ensure the requested delivery day is configured for this customer."""
    if delivery_day not in customer.delivery_days:
        raise HTTPException(
            400,
            "Deze leverdag is niet ingesteld als mogelijke leverdag voor deze klant.",
        )


def _default_delivery_day(customer: Customer) -> str:
    if customer.delivery_day in customer.delivery_days:
        return customer.delivery_day
    return customer.delivery_days[0]


def _current_iso_week() -> str:
    today = datetime.date.today()
    return f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"


@router.post("", response_model=OrderResponse)
def create_order(
    body: ManualOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Create an order by picking existing customers and SKUs."""
    org_id = _resolve_organization_id(user, body.organization_id, db)

    # Customer-role users can only order for their linked customer,
    # and may only pick SKUs already assigned to that customer.
    if user.role == "customer" and not user.customer_id:
        raise HTTPException(
            403,
            "Klantgebruikers moeten aan een klant gekoppeld zijn om orders te plaatsen",
        )

    if user.role == "customer" and user.customer_id:
        assigned_skus = _customer_assigned_sku_ids(db, user.customer_id)
        for line in body.lines:
            if line.customer_id != user.customer_id:
                raise HTTPException(
                    403,
                    "Klantgebruikers kunnen alleen orders plaatsen voor hun eigen klant",
                )
            if line.sku_id not in assigned_skus:
                raise HTTPException(
                    403,
                    "Klantgebruikers kunnen geen nieuwe wijnen toevoegen aan een order",
                )

    ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    # New orders await merchant approval; the delivery week is assigned at
    # the moment of approval, not at creation.
    order = Order(
        organization_id=org_id,
        created_by=user.id,
        reference=ref,
        status="pending_approval",
        remarks=body.remarks,
        delivery_week=None,
    )
    db.add(order)
    db.flush()

    # Group by (customer_id, sku_id), sum quantities; track delivery_day per customer
    line_quantities: dict[tuple[int, int], int] = {}
    customer_delivery_days: dict[int, str | None] = {}
    for line in body.lines:
        key = (line.customer_id, line.sku_id)
        line_quantities[key] = line_quantities.get(key, 0) + line.quantity
        if line.delivery_day is not None:
            customer_delivery_days[line.customer_id] = line.delivery_day

    # Enforce 1 order = 1 customer
    distinct_customer_ids = {cid for cid, _ in line_quantities.keys()}
    if len(distinct_customer_ids) > 1:
        raise HTTPException(400, "Een order kan maar voor één klant zijn")

    sku_cache: dict[int, SKU] = {}
    customer_cache: dict[int, Customer] = {}
    customer_sku_pairs: set[tuple[int, int]] = set()

    for (customer_id, sku_id), qty in line_quantities.items():
        customer = customer_cache.get(customer_id) or db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404, f"Klant met id {customer_id} niet gevonden")
        customer_cache[customer_id] = customer
        sku = sku_cache.get(sku_id) or db.get(SKU, sku_id)
        if not sku:
            raise HTTPException(404, f"SKU met id {sku_id} niet gevonden")
        sku_cache[sku_id] = sku

        # Use explicitly chosen delivery_day, fall back to customer default
        delivery_day = customer_delivery_days.get(customer_id) or _default_delivery_day(customer)
        _check_delivery_day_allowed(delivery_day, customer)

        db.add(OrderLine(
            order_id=order.id,
            sku_id=sku_id,
            customer_id=customer_id,
            klant=customer.name,
            quantity=qty,
            delivery_day=delivery_day,
        ))
        customer_sku_pairs.add((customer_id, sku_id))

    # Auto-populate customer_skus catalog
    _upsert_customer_skus(db, customer_sku_pairs)

    db.commit()
    db.refresh(order)

    publish_event(
        "order_created_manual",
        details={"order_reference": ref, "total_lines": len(line_quantities)},
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db, hide_prices=user.role == "courier")


@router.get("", response_model=list[OrderResponse])
def list_orders(
    week: str | None = None,
    include_history: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List orders based on user role.

    - Platform admin: all orders
    - Org owner/member: orders for their organization
    - Customer: only their own orders
    - Courier: all open orders (for delivery) including read-only visibility
      of orders awaiting merchant approval, plus completed/cancelled orders
      when include_history=true

    Optional ``week`` filter (e.g. "2026-W16") restricts to that delivery week.
    """
    query = db.query(Order)

    if user.is_platform_admin:
        pass  # See everything
    elif user.role == "courier":
        if include_history:
            query = query.filter(Order.status.in_((
                "pending_approval", "pending_images", "active",
                "completed", "cancelled", "closed",
            )))
        else:
            query = query.filter(
                Order.status.in_(("pending_approval", "pending_images", "active"))
            )
    elif user.role == "customer":
        if not user.customer_id:
            return []
        query = query.filter(
            Order.id.in_(
                db.query(OrderLine.order_id).filter(
                    OrderLine.customer_id == user.customer_id
                )
            )
        )
    elif user.organization_id:
        query = query.filter(Order.organization_id == user.organization_id)
    else:
        return []

    if week:
        query = query.filter(Order.delivery_week == week)

    orders = query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()
    return [
        _order_to_response(o, db, hide_prices=user.role == "courier")
        for o in orders
    ]


# ---------------------------------------------------------------------------
# Weekly order summary per supplier
# ---------------------------------------------------------------------------

def _parse_iso_week(week_str: str) -> tuple[datetime.date, datetime.date]:
    """Parse an ISO week string like '2026-W15' into (monday, sunday) dates."""
    try:
        monday = datetime.datetime.strptime(week_str + "-1", "%G-W%V-%u").date()
    except ValueError:
        raise HTTPException(400, f"Ongeldig weekformaat: '{week_str}'. Gebruik bijv. '2026-W15'.")
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


@router.get("/weekly-pick-photos", response_model=list[WeeklyPickPhotoResponse])
def weekly_pick_photos(
    week: str = Query(None, description="ISO week, bijv. '2026-W15'. Standaard: huidige week."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Photos for order lines that are not fully picked when this view opens."""
    if not week:
        today = datetime.date.today()
        week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"

    monday, sunday = _parse_iso_week(week)
    start_dt = datetime.datetime.combine(monday, datetime.time.min)
    end_dt = datetime.datetime.combine(sunday, datetime.time.max)

    query = (
        db.query(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .options(
            selectinload(OrderLine.sku).selectinload(SKU.reference_images),
            selectinload(OrderLine.customer),
        )
        .filter(
            Order.status == "active",
            OrderLine.booked_count < OrderLine.quantity,
            or_(
                Order.delivery_week == week,
                (Order.delivery_week.is_(None)) & (Order.created_at >= start_dt) & (Order.created_at <= end_dt),
            ),
        )
    )

    if user.is_platform_admin:
        pass
    elif user.role == "courier":
        pass
    elif user.organization_id:
        query = query.filter(Order.organization_id == user.organization_id)
    else:
        return []

    lines = query.all()
    by_sku: dict[int, list[OrderLine]] = defaultdict(list)
    for line in lines:
        by_sku[line.sku_id].append(line)

    items: list[WeeklyPickPhotoResponse] = []
    for sku_lines in by_sku.values():
        line = sku_lines[0]
        image = next(
            (
                img
                for img in sorted(
                    line.sku.reference_images,
                    key=lambda img: img.created_at or datetime.datetime.min,
                )
                if img.processing_status == "done" and img.image_path
            ),
            None,
        )
        image_url = f"/api/thumbnails/320/{image.image_path}" if image else None
        customers = sorted(
            {
                l.customer_name
                for l in sku_lines
                if l.booked_count < l.quantity and l.customer_name
            },
            key=str.lower,
        )
        items.append(
            WeeklyPickPhotoResponse(
                order_line_id=line.id,
                sku_id=line.sku_id,
                wine_name=line.sku.name,
                image_url=image_url,
                quantity=sum(l.quantity for l in sku_lines),
                booked_count=sum(l.booked_count for l in sku_lines),
                customers=customers,
            )
        )

    return sorted(items, key=lambda item: item.wine_name.lower())



def _build_customer_response(
    week: str,
    enriched: list[dict],
) -> WeeklySummaryResponse:
    """Pivot enriched lines into Customer -> SKU lines for invoicing."""
    # customer_id (or None) keyed; name kept alongside for display + sort
    customer_groups: dict[tuple[int | None, str], dict[int, dict]] = defaultdict(dict)

    for e in enriched:
        key = (e["customer_id"], e["customer_name"])
        per_sku = customer_groups[key]
        sku_id = e["sku"].id
        existing = per_sku.get(sku_id)
        if existing is None:
            per_sku[sku_id] = {
                "sku": e["sku"],
                "quantity": e["quantity"],
                "effective_price": e["effective_price"],
                "remarks": e["remarks"],
            }
        else:
            existing["quantity"] += e["quantity"]
            if e["remarks"] and e["remarks"] not in existing["remarks"]:
                existing["remarks"] = (
                    f"{existing['remarks']}; {e['remarks']}"
                    if existing["remarks"]
                    else e["remarks"]
                )

    customers_out: list[WeeklySummaryCustomer] = []
    grand_total_qty = 0
    grand_total_boxes = 0
    grand_total_bottles = 0
    grand_total_val = 0.0

    for (cust_id, cust_name), sku_map in sorted(
        customer_groups.items(), key=lambda x: x[0][1].lower()
    ):
        lines_out: list[WeeklySummaryCustomerLine] = []
        cust_qty = 0
        cust_boxes = 0
        cust_bottles = 0
        cust_val = 0.0
        for sku_id, agg in sorted(
            sku_map.items(), key=lambda kv: kv[1]["sku"].name.lower()
        ):
            qty = agg["quantity"]
            ep = agg["effective_price"]
            lt = round(ep * qty, 2) if ep is not None else None
            lines_out.append(WeeklySummaryCustomerLine(
                sku_id=sku_id,
                sku_code=agg["sku"].sku_code,
                sku_name=agg["sku"].name,
                is_bottle=agg["sku"].is_bottle,
                quantity=qty,
                effective_price=ep,
                line_total=lt,
                remarks=agg["remarks"],
            ))
            cust_qty += qty
            if agg["sku"].is_bottle:
                cust_bottles += qty
            else:
                cust_boxes += qty
            if lt is not None:
                cust_val += lt

        customers_out.append(WeeklySummaryCustomer(
            customer_id=cust_id,
            customer_name=cust_name,
            lines=lines_out,
            customer_total_quantity=cust_qty,
            customer_total_boxes=cust_boxes,
            customer_total_bottles=cust_bottles,
            customer_total_value=round(cust_val, 2) if cust_val else None,
        ))
        grand_total_qty += cust_qty
        grand_total_boxes += cust_boxes
        grand_total_bottles += cust_bottles
        grand_total_val += cust_val

    return WeeklySummaryResponse(
        week=week,
        group_by="customer",
        suppliers=[],
        customers=customers_out,
        grand_total_quantity=grand_total_qty,
        grand_total_boxes=grand_total_boxes,
        grand_total_bottles=grand_total_bottles,
        grand_total_value=round(grand_total_val, 2) if grand_total_val else None,
    )


@router.get("/reports/monthly-boxes", response_model=MonthlyBoxesResponse)
def monthly_booked_boxes(
    organization_id: int | None = Query(
        None, description="Optioneel: beperk tot één handelaar."
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Aantal geboekte dozen per maand voor voltooide en gesloten orders.

    Een doos telt mee in de maand waarin de order is afgerond (``finalized_at``).
    Het aantal is het werkelijk geboekte aantal: een order die op 10/12 wordt
    gesloten draagt 10 dozen bij. Gegroepeerd per handelaar (organisatie).

    Zichtbaar voor de koerier en platform-admin (alle handelaren) en voor een
    owner/member (alleen de eigen handelaar).
    """
    # Per finalized order: its organization, finalize moment and booked boxes.
    query = (
        db.query(
            Order.organization_id.label("org_id"),
            Order.finalized_at.label("finalized_at"),
            SKU.is_bottle.label("is_bottle"),
            func.coalesce(func.sum(OrderLine.booked_count), 0).label("units"),
        )
        .join(OrderLine, OrderLine.order_id == Order.id)
        .join(SKU, OrderLine.sku_id == SKU.id)
        .filter(
            Order.status.in_(("completed", "closed")),
            Order.finalized_at.isnot(None),
        )
        .group_by(Order.id, Order.organization_id, Order.finalized_at, SKU.is_bottle)
    )

    if user.is_platform_admin or user.role == "courier":
        if organization_id is not None:
            query = query.filter(Order.organization_id == organization_id)
    elif user.organization_id and user.role in ("owner", "member"):
        query = query.filter(Order.organization_id == user.organization_id)
    else:
        raise HTTPException(403, "Geen toegang tot dit overzicht")

    # Bucket booked units into (organization, "YYYY-MM"), boxes and bottles apart.
    buckets: dict[int | None, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"boxes": 0, "bottles": 0})
    )
    for row in query.all():
        if not row.units or row.finalized_at is None:
            continue
        month = row.finalized_at.strftime("%Y-%m")
        unit = "bottles" if row.is_bottle else "boxes"
        buckets[row.org_id][month][unit] += int(row.units)

    if not buckets:
        return MonthlyBoxesResponse(organizations=[])

    # Resolve organization names in one query.
    org_ids = [oid for oid in buckets if oid is not None]
    name_by_id: dict[int, str] = {}
    if org_ids:
        name_by_id = {
            o.id: o.name
            for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all()
        }

    organizations: list[MonthlyBoxesOrganization] = []
    for org_id, months in buckets.items():
        org_name = (
            name_by_id.get(org_id, f"Handelaar #{org_id}")
            if org_id is not None
            else "Zonder handelaar"
        )
        month_rows = [
            MonthlyBoxesMonth(month=m, boxes=v["boxes"], bottles=v["bottles"])
            for m, v in sorted(months.items(), reverse=True)
        ]
        organizations.append(
            MonthlyBoxesOrganization(
                organization_id=org_id,
                organization_name=org_name,
                total_boxes=sum(v["boxes"] for v in months.values()),
                total_bottles=sum(v["bottles"] for v in months.values()),
                months=month_rows,
            )
        )

    organizations.sort(key=lambda o: o.organization_name.lower())
    return MonthlyBoxesResponse(organizations=organizations)


@router.get("/weekly-summary", response_model=WeeklySummaryResponse)
def weekly_order_summary(
    week: str = Query(None, description="ISO week, bijv. '2026-W15'. Standaard: huidige week."),
    group_by: str = Query("supplier", description="Groepering: 'supplier' of 'customer'."),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
):
    """Weekly order summary grouped by supplier or by customer (for invoicing).

    Merchant-only: exposes pricing across all customers in the org, so customers
    and couriers are denied (see require_merchant).
    """
    if group_by not in ("supplier", "customer"):
        raise HTTPException(400, "group_by moet 'supplier' of 'customer' zijn")
    if not user.is_platform_admin and not user.organization_id:
        raise HTTPException(400, "Gebruiker heeft geen organisatie")

    # Determine week range
    if not week:
        today = datetime.date.today()
        week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    monday, sunday = _parse_iso_week(week)

    org_id = user.organization_id

    # Fetch all order lines for the delivery week
    start_dt = datetime.datetime.combine(monday, datetime.time.min)
    end_dt = datetime.datetime.combine(sunday, datetime.time.max)

    query = (
        db.query(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .options(
            selectinload(OrderLine.sku).selectinload(SKU.supplier),
            selectinload(OrderLine.customer),
            selectinload(OrderLine.order),
        )
        .filter(
            # Only approved orders count toward the weekly summary; orders
            # awaiting approval have no delivery week yet.
            Order.status.in_(("pending_images", "active")),
            or_(
                Order.delivery_week == week,
                # Fallback for legacy orders without delivery_week
                (Order.delivery_week.is_(None)) & (Order.created_at >= start_dt) & (Order.created_at <= end_dt),
            ),
        )
    )
    if not user.is_platform_admin:
        query = query.filter(Order.organization_id == org_id)

    lines = query.all()

    if not lines:
        return WeeklySummaryResponse(
            week=week,
            group_by=group_by,
            suppliers=[],
            customers=[],
            grand_total_quantity=0,
            grand_total_value=0,
        )

    # Batch-load customer prices
    customer_sku_keys = {
        (l.customer_id, l.sku_id) for l in lines if l.customer_id is not None
    }
    customer_price_map: dict[tuple[int, int], CustomerSKU] = {}
    if customer_sku_keys:
        c_ids = sorted({cid for cid, _ in customer_sku_keys})
        s_ids = sorted({sid for _, sid in customer_sku_keys})
        links = (
            db.query(CustomerSKU)
            .filter(CustomerSKU.customer_id.in_(c_ids), CustomerSKU.sku_id.in_(s_ids))
            .all()
        )
        customer_price_map = {
            (lk.customer_id, lk.sku_id): lk
            for lk in links
            if (lk.customer_id, lk.sku_id) in customer_sku_keys
        }

    # Enrich each line with supplier + price info (shared by both groupings).
    enriched: list[dict] = []
    for line in lines:
        sku = line.sku
        supplier_id = sku.supplier_id
        supplier_name = sku.supplier.name if sku.supplier else "Geen leverancier toegewezen"

        default_price = float(sku.default_price) if sku.default_price is not None else None
        link = customer_price_map.get((line.customer_id, line.sku_id)) if line.customer_id else None
        unit_price = float(link.unit_price) if link and link.unit_price is not None else None
        discount_type = link.discount_type if link else None
        discount_value = float(link.discount_value) if link and link.discount_value is not None else None
        customer_discount = (
            float(line.customer.discount_percentage)
            if line.customer is not None and line.customer.discount_percentage is not None
            else None
        )
        effective_price = calc_effective_price(
            default_price, unit_price, discount_type, discount_value, customer_discount
        )

        enriched.append({
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "customer_id": line.customer_id,
            "customer_name": line.customer_name,
            "sku": sku,
            "quantity": line.quantity,
            "effective_price": effective_price,
            "default_price": default_price,
            "remarks": line.order.remarks or "",
        })

    if group_by == "customer":
        return _build_customer_response(week, enriched)

    # Group: supplier -> sku -> list of (customer_name, quantity, effective_price)
    supplier_groups: dict[tuple[int | None, str], dict[int, list]] = defaultdict(lambda: defaultdict(list))

    for e in enriched:
        supplier_groups[(e["supplier_id"], e["supplier_name"])][e["sku"].id].append(e)

    # Build response
    suppliers_out: list[WeeklySummarySupplier] = []
    grand_total_qty = 0
    grand_total_boxes = 0
    grand_total_bottles = 0
    grand_total_val = 0.0

    for (sup_id, sup_name), sku_map in sorted(supplier_groups.items(), key=lambda x: x[0][1]):
        wines_out: list[WeeklySummaryWine] = []
        sup_qty = 0
        sup_boxes = 0
        sup_bottles = 0
        sup_val = 0.0

        for sku_id, entries in sku_map.items():
            sku_obj = entries[0]["sku"]
            customer_agg: dict[str, dict] = {}
            for e in entries:
                cname = e["customer_name"]
                if cname in customer_agg:
                    customer_agg[cname]["quantity"] += e["quantity"]
                    if e["remarks"]:
                        existing = customer_agg[cname].get("remarks", "")
                        if e["remarks"] not in existing:
                            customer_agg[cname]["remarks"] = (
                                f"{existing}; {e['remarks']}" if existing else e["remarks"]
                            )
                else:
                    customer_agg[cname] = {
                        "quantity": e["quantity"],
                        "effective_price": e["effective_price"],
                        "remarks": e.get("remarks", ""),
                    }

            orders_out = []
            wine_total = 0.0
            wine_qty = 0
            for cname, agg in customer_agg.items():
                qty = agg["quantity"]
                ep = agg["effective_price"]
                lt = round(ep * qty, 2) if ep is not None else None
                orders_out.append(WeeklySummaryCustomerOrder(
                    customer_name=cname,
                    quantity=qty,
                    effective_price=ep,
                    line_total=lt,
                    remarks=agg.get("remarks", ""),
                ))
                wine_qty += qty
                if lt is not None:
                    wine_total += lt

            wines_out.append(WeeklySummaryWine(
                sku_id=sku_id,
                sku_code=sku_obj.sku_code,
                sku_name=sku_obj.name,
                default_price=entries[0]["default_price"],
                is_bottle=sku_obj.is_bottle,
                total_quantity=wine_qty,
                orders=orders_out,
                wine_total=round(wine_total, 2) if wine_total else None,
            ))
            sup_qty += wine_qty
            if sku_obj.is_bottle:
                sup_bottles += wine_qty
            else:
                sup_boxes += wine_qty
            sup_val += wine_total

        suppliers_out.append(WeeklySummarySupplier(
            supplier_id=sup_id,
            supplier_name=sup_name,
            wines=wines_out,
            supplier_total_quantity=sup_qty,
            supplier_total_boxes=sup_boxes,
            supplier_total_bottles=sup_bottles,
            supplier_total_value=round(sup_val, 2) if sup_val else None,
        ))
        grand_total_qty += sup_qty
        grand_total_boxes += sup_boxes
        grand_total_bottles += sup_bottles
        grand_total_val += sup_val

    return WeeklySummaryResponse(
        week=week,
        group_by="supplier",
        suppliers=suppliers_out,
        customers=[],
        grand_total_quantity=grand_total_qty,
        grand_total_boxes=grand_total_boxes,
        grand_total_bottles=grand_total_bottles,
        grand_total_value=round(grand_total_val, 2) if grand_total_val else None,
    )


def _next_pick_image_url(line: OrderLine) -> str | None:
    """First processed reference image for this SKU, as a thumbnail URL.

    Same selection as weekly_pick_photos so the suggestion photo matches the
    rest of the pick UI.
    """
    image = next(
        (
            img
            for img in sorted(
                line.sku.reference_images,
                key=lambda img: img.created_at or datetime.datetime.min,
            )
            if img.processing_status == "done" and img.image_path
        ),
        None,
    )
    return f"/api/thumbnails/320/{image.image_path}" if image else None


def _to_next_pick(line: OrderLine, order: Order, source: str) -> NextPickResponse:
    return NextPickResponse(
        sku_id=line.sku_id,
        sku_name=line.sku.name,
        order_line_id=line.id,
        image_url=_next_pick_image_url(line),
        remaining_quantity=max(line.quantity - line.booked_count, 0),
        source=source,
        order_id=order.id,
        customer_name=line.customer_name,
    )


def _first_open_line(order: Order, source: str) -> NextPickResponse | None:
    """Next pickable line in order-line id order, or None if fully booked."""
    for line in sorted(order.lines, key=lambda l: l.id):
        if line.booked_count < line.quantity:
            return _to_next_pick(line, order, source)
    return None


@router.get("/{order_id}/next-pick", response_model=NextPickResponse | None)
def next_pick(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Suggestion photo for the next SKU to scan.

    Within the order while it still has open lines; once it is fully booked,
    falls back to the first open line of another active scheduled order in the
    same organization, across all weeks — matching the booking sweep in
    receiving._open_scope_lines_query so any suggestion is actually bookable.
    No status gate on the context order: we want a suggestion exactly when the
    order has just been completed.
    """
    pick_options = (
        selectinload(Order.lines).selectinload(OrderLine.sku).selectinload(SKU.reference_images),
        selectinload(Order.lines).selectinload(OrderLine.customer),
    )
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .options(*pick_options)
        .first()
    )
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    # Access: platform admins and couriers see everything; org owner/member
    # only their own organization. Customers have no business in the pick flow.
    if user.role == "customer":
        raise HTTPException(403, "Geen toegang tot deze order")
    if not user.is_platform_admin and user.role != "courier":
        if not user.organization_id or order.organization_id != user.organization_id:
            raise HTTPException(403, "Geen toegang tot deze order")

    hit = _first_open_line(order, "this_order")
    if hit:
        return hit

    others = (
        db.query(Order)
        .filter(
            Order.id != order.id,
            Order.status == "active",
            Order.organization_id == order.organization_id,
            Order.delivery_week.isnot(None),
        )
        .order_by(Order.delivery_week, Order.id)
        .options(*pick_options)
        .all()
    )
    for other in others:
        hit = _first_open_line(other, "other_order")
        if hit:
            return hit

    return None


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
        if user.role == "customer" and not _customer_can_view_order(user, order):
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.role == "courier" and order.status not in COURIER_VIEWABLE_STATUSES:
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.organization_id and order.organization_id != user.organization_id:
            if user.role != "courier":
                raise HTTPException(403, "Geen toegang tot deze order")

    return _order_to_response(order, db, hide_prices=user.role == "courier")


@router.post("/{order_id}/approve", response_model=OrderResponse)
def approve_order(
    order_id: int,
    body: OrderApprove | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Approve an order so the courier can start working on it.

    On approval the delivery week is fixed: by default the ISO week of
    today (an order approved on Friday is delivered that same week), or an
    explicitly chosen week. The order becomes ``active``, or
    ``pending_images`` when SKU reference images are still missing.

    A ``pending_images`` order can be approved again once that is resolved
    (or deliberately without images: wines that arrive at the warehouse
    without a reference photo are surfaced at scan time, where the worker
    can capture the photo on the spot). The delivery week assigned at first
    approval is kept in that case.
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    if not user.is_platform_admin and user.role not in ("owner", "member"):
        raise HTTPException(403, "Alleen handelaren kunnen orders goedkeuren")
    if not user.is_platform_admin and order.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang tot deze order")

    if order.status not in ("pending_approval", "pending_images"):
        raise HTTPException(400, f"Order kan niet goedgekeurd worden (status: {order.status})")

    week = body.week if body else None
    if week:
        _parse_iso_week(week)  # validates the format, raises 400 otherwise

    if order.status == "pending_approval":
        order.delivery_week = week or _current_iso_week()
    elif week:
        # pending_images → active: explicit activation, images optional.
        order.delivery_week = week

    # Optionally peel off the lines that still lack a reference image onto a new
    # sibling order, so the rest of this order can go active right away.
    sibling: Order | None = None
    if body and body.split_unimaged:
        sibling = _split_unimaged_lines(order, db)

    if order.status == "pending_approval":
        all_have_images = all(
            len(line.sku.reference_images) > 0 for line in order.lines
        )
        order.status = "active" if all_have_images else "pending_images"
    else:
        order.status = "active"

    db.commit()
    db.refresh(order)

    publish_event(
        "order_approved",
        details={
            "order_reference": order.reference,
            "new_status": order.status,
            "split_order_reference": sibling.reference if sibling else None,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db, hide_prices=user.role == "courier")


def _split_unimaged_lines(order: Order, db: Session) -> "Order | None":
    """Move lines whose SKU lacks a reference image to a new sibling order.

    Only unbooked lines move; a line that already has bookings stays put so no
    picking progress is lost. Returns the new ``pending_images`` order, or None
    when there is nothing to split (no unimaged lines, or every line is unimaged
    so the original would be left empty).
    """
    to_move = [
        line
        for line in order.lines
        if len(line.sku.reference_images) == 0 and line.booked_count == 0
    ]
    if not to_move or len(to_move) == len(order.lines):
        return None

    sibling = Order(
        organization_id=order.organization_id,
        created_by=order.created_by,
        reference=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        status="pending_images",
        remarks=(f"Afgesplitst van {order.reference} (SKU's zonder beeld)").strip(),
        delivery_week=order.delivery_week,
    )
    db.add(sibling)
    db.flush()

    for line in to_move:
        line.order_id = sibling.id

    # The lines collections are now stale; reload them so the caller sees each
    # order's real remaining lines (the status check depends on it).
    db.flush()
    db.expire(order, ["lines"])
    db.expire(sibling, ["lines"])

    return sibling


@router.post("/{order_id}/close", response_model=OrderResponse)
def close_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Close an active order even if it is not fully picked.

    Bookings that were already made stay intact; the remaining open lines are
    simply no longer expected. A closed order drops out of the scan scope, so
    couriers stop receiving matches for it. Closing is available to the courier
    and to the owning organization (and platform admins).
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    allowed = (
        user.is_platform_admin
        or user.role == "courier"
        or (
            user.role in ("owner", "member")
            and order.organization_id == user.organization_id
        )
    )
    if not allowed:
        raise HTTPException(403, "Geen toegang om deze order te sluiten")

    if order.status not in ("active", "pending_images"):
        raise HTTPException(400, f"Order kan niet gesloten worden (status: {order.status})")

    order.status = "closed"
    order.mark_finalized()
    db.commit()
    db.refresh(order)

    publish_event(
        "order_closed",
        details={
            "order_reference": order.reference,
            "total_boxes": sum(l.quantity for l in order.lines),
            "booked_boxes": sum(l.booked_count for l in order.lines),
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db, hide_prices=user.role == "courier")


@router.patch("/{order_id}", response_model=OrderResponse)
def update_order(
    order_id: int,
    body: OrderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update order remarks. Allowed in any status."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    if not user.is_platform_admin:
        if user.organization_id and order.organization_id != user.organization_id:
            raise HTTPException(403, "Geen toegang tot deze order")

    order.remarks = body.remarks
    db.commit()
    db.refresh(order)
    return _order_to_response(order, db, hide_prices=user.role == "courier")


def _assert_can_delete_order(order: Order, user: User) -> None:
    """Guard order deletion. Only platform admins and merchants (owner/member)
    of the owning organization may delete, and only while the order is still
    being prepared with nothing scanned yet. Customers may never delete orders.
    """
    if user.is_platform_admin:
        return
    if user.role not in ("owner", "member"):
        raise HTTPException(403, "Geen toegang om deze order te verwijderen")
    if user.organization_id and order.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang tot deze order")
    if order.status not in EDITABLE_STATUSES:
        raise HTTPException(
            409, f"Order kan niet verwijderd worden (status: {order.status})"
        )
    if any(line.booked_count > 0 for line in order.lines):
        raise HTTPException(
            409, "Kan order niet verwijderen — er zijn al dozen gescand"
        )


@router.delete("/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Delete an order and all its lines.

    Platform admins may delete any order. A merchant (owner/member) may delete
    an order of their own organization while it is still being prepared
    (pending_approval/pending_images) and nothing has been scanned yet.
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

    _assert_can_delete_order(order, user)

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


# ---------------------------------------------------------------------------
# Order line management
# ---------------------------------------------------------------------------

EDITABLE_STATUSES = ("pending_approval", "pending_images")
ADDABLE_STATUSES = ("pending_approval", "pending_images", "active")


def _get_editable_order(order_id: int, db: Session, user: User) -> Order:
    """Fetch an order and check access. Raises 404/403 if not found or forbidden."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if not user.is_platform_admin:
        if user.organization_id and order.organization_id != user.organization_id:
            raise HTTPException(403, "Geen toegang tot deze order")
        if user.role == "customer" and order.created_by != user.id:
            raise HTTPException(403, "Geen toegang tot deze order")
    return order


def _recompute_order_status(order: Order) -> None:
    """Recompute order status based on SKU images and booking progress.

    Orders awaiting approval stay pending_approval regardless of line edits;
    approval is an explicit merchant action.
    """
    if order.status in ("completed", "cancelled", "closed", "pending_approval"):
        return
    all_have_images = all(len(l.sku.reference_images) > 0 for l in order.lines)
    all_booked = all(l.booked_count >= l.quantity for l in order.lines)
    if order.status == "active":
        if all_booked:
            order.status = "completed"
            order.mark_finalized()
        return
    # Approved but waiting for images: promote once every SKU has one.
    if all_have_images:
        order.status = "active"


@router.post("/{order_id}/lines", response_model=OrderResponse)
def add_order_line(
    order_id: int,
    body: OrderLineAdd,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Add a line to an order. Allowed on pending_approval, pending_images, and active orders."""
    order = _get_editable_order(order_id, db, user)

    if order.status not in ADDABLE_STATUSES:
        raise HTTPException(
            409, f"Kan geen regels toevoegen aan een order met status '{order.status}'"
        )

    # Customer-role users can only add for their linked customer,
    # and only SKUs already assigned to that customer.
    if user.role == "customer" and user.customer_id:
        if body.customer_id != user.customer_id:
            raise HTTPException(403, "Klantgebruikers kunnen alleen voor hun eigen klant bestellen")
        assigned_skus = _customer_assigned_sku_ids(db, user.customer_id)
        if body.sku_id not in assigned_skus:
            raise HTTPException(
                403,
                "Klantgebruikers kunnen geen nieuwe wijnen toevoegen aan een order",
            )

    customer = db.get(Customer, body.customer_id)
    if not customer:
        raise HTTPException(404, f"Klant met id {body.customer_id} niet gevonden")
    sku = db.get(SKU, body.sku_id)
    if not sku:
        raise HTTPException(404, f"SKU met id {body.sku_id} niet gevonden")

    delivery_day = body.delivery_day or _default_delivery_day(customer)
    _check_delivery_day_allowed(delivery_day, customer)

    # Check if a line for this (customer, sku) already exists — merge quantities
    existing_line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == order_id,
            OrderLine.customer_id == body.customer_id,
            OrderLine.sku_id == body.sku_id,
        )
        .first()
    )
    if existing_line:
        existing_line.quantity += body.quantity
        existing_line.delivery_day = delivery_day
    else:
        db.add(OrderLine(
            order_id=order_id,
            sku_id=body.sku_id,
            customer_id=body.customer_id,
            klant=customer.name,
            quantity=body.quantity,
            delivery_day=delivery_day,
        ))

    _upsert_customer_skus(db, {(body.customer_id, body.sku_id)})
    _recompute_order_status(order)
    db.commit()
    db.refresh(order)

    publish_event(
        "order_line_added",
        details={
            "order_reference": order.reference,
            "sku_id": body.sku_id,
            "customer_id": body.customer_id,
            "quantity": body.quantity,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )
    return _order_to_response(order, db, hide_prices=user.role == "courier")


@router.patch("/{order_id}/lines/{line_id}", response_model=OrderResponse)
def update_order_line(
    order_id: int,
    line_id: int,
    body: OrderLineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Update quantity of an order line.

    - Pending_approval/pending_images: quantity can be freely changed (>= 1).
    - Active: quantity can only be increased (not decreased below booked_count).
    """
    order = _get_editable_order(order_id, db, user)

    if order.status not in ADDABLE_STATUSES:
        raise HTTPException(
            409, f"Kan geen regels wijzigen op een order met status '{order.status}'"
        )

    line = (
        db.query(OrderLine)
        .filter(OrderLine.id == line_id, OrderLine.order_id == order_id)
        .first()
    )
    if not line:
        raise HTTPException(404, "Orderregel niet gevonden")

    if body.quantity < line.booked_count:
        raise HTTPException(
            409,
            f"Kan hoeveelheid niet verlagen onder het aantal al gescande dozen ({line.booked_count})",
        )

    if order.status == "active" and body.quantity < line.quantity:
        raise HTTPException(
            409, "Kan hoeveelheid niet verlagen op een actieve order — alleen verhogen is toegestaan"
        )

    line.quantity = body.quantity
    _recompute_order_status(order)
    db.commit()
    db.refresh(order)

    publish_event(
        "order_line_updated",
        details={
            "order_reference": order.reference,
            "line_id": line_id,
            "new_quantity": body.quantity,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )
    return _order_to_response(order, db, hide_prices=user.role == "courier")


@router.delete("/{order_id}/lines/{line_id}", response_model=OrderLineDeleteResponse)
def delete_order_line(
    order_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_can_create_orders),
):
    """Delete an order line. Only allowed on pending_approval/pending_images orders with no bookings.

    Removing the last remaining line deletes the whole order; the response then
    has ``order_deleted`` set instead of an updated order.
    """
    order = _get_editable_order(order_id, db, user)

    if order.status not in EDITABLE_STATUSES:
        raise HTTPException(
            409, f"Kan geen regels verwijderen van een order met status '{order.status}'"
        )

    line = (
        db.query(OrderLine)
        .filter(OrderLine.id == line_id, OrderLine.order_id == order_id)
        .first()
    )
    if not line:
        raise HTTPException(404, "Orderregel niet gevonden")

    if line.booked_count > 0:
        raise HTTPException(
            409, f"Kan regel niet verwijderen — er zijn al {line.booked_count} dozen gescand"
        )

    # Removing the final line means the whole order is gone — same restriction
    # as deleting the order outright, so customers cannot bypass it here.
    if len(order.lines) <= 1:
        _assert_can_delete_order(order, user)
        reference = order.reference
        order_pk = order.id
        db.delete(order)
        db.commit()

        publish_event(
            "order_deleted",
            details={"order_reference": reference},
            user=user,
            resource_type="order",
            resource_id=order_pk,
        )
        return OrderLineDeleteResponse(order_deleted=True)

    db.delete(line)
    _recompute_order_status(order)
    db.commit()
    db.refresh(order)

    publish_event(
        "order_line_removed",
        details={
            "order_reference": order.reference,
            "line_id": line_id,
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )
    return OrderLineDeleteResponse(order=_order_to_response(order, db, hide_prices=user.role == "courier"))


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
        if user.role == "customer" and not _customer_can_view_order(user, order):
            raise HTTPException(403, "Geen toegang tot deze order")
        elif user.role == "courier" and order.status not in COURIER_VIEWABLE_STATUSES:
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
