"""Order management: manual order creation and lifecycle."""

import datetime
import logging
import uuid
from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, selectinload

from app.auth import (
    get_current_user,
    require_can_create_orders,
    require_merchant,
    require_module,
)
from app.database import get_db
from app.events import publish_event
from app.models import (
    Customer,
    CustomerSKU,
    InventoryBalance,
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
    ReplenishmentOrderCreate,
    WeeklyPickPhotoResponse,
    WeeklySummaryCustomer,
    WeeklySummaryCustomerLine,
    WeeklySummaryCustomerOrder,
    WeeklySummaryResponse,
    WeeklySummarySupplier,
    WeeklySummaryWine,
)
from app.services.booking import recompute_order_status, remaining_for_line
from app.services.inventory_sync import push_inventory_to_channels
from app.services.pricing import calc_effective_price
from app.services.push_notifications import (
    enqueue_approved_order_ready,
    enqueue_customer_order_created,
)
from app.services.stock import LOCATION_LABELS, adjust_reservation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])

# Statuses a courier may view (matches the courier order list). Orders
# awaiting merchant approval are visible read-only, so the courier can see
# what work may be coming.
COURIER_VIEWABLE_STATUSES = (
    "pending_approval", "active", "completed", "shipped", "cancelled", "closed",
)

# The warehouse (and the invoice built on the monthly report) runs on local time,
# while timestamps are stored as naive UTC.
WAREHOUSE_TZ = ZoneInfo("Europe/Amsterdam")

# Mirrors receiving._DELIVERY_DAY_SORT so the next-pick suggestion is selected
# in the same order book_box actually books, keeping the card truthful.
_DELIVERY_DAY_SORT = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
}


def _as_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _report_month(finalized_at: datetime.datetime) -> str:
    """Bucket a finalize moment into its warehouse month ("YYYY-MM").

    ``finalized_at`` is stored as naive UTC (see Order.mark_finalized), while the
    warehouse — and the invoice built on this report — works in local time. An
    evening pick just after midnight local (22:xx UTC in summer) belongs to the
    month the courier actually worked, not to the UTC one.
    """
    if finalized_at.tzinfo is None:
        finalized_at = finalized_at.replace(tzinfo=datetime.timezone.utc)
    return finalized_at.astimezone(WAREHOUSE_TZ).strftime("%Y-%m")


def _primary_location_code(sku) -> str | None:
    """Scannable code of a barcode product's primary pick location, if any.

    Only *active* locations count — an inactive one would send the picker into
    the location phase for a shelf that /scan-location then rejects with a 404.
    Prefers the ``is_primary`` link; falls back to the first active one. NULL for
    vision products and barcode products not on an active shelf.
    """
    active = [
        lnk
        for lnk in (getattr(sku, "location_links", None) or [])
        if lnk.location and lnk.location.active
    ]
    if not active:
        return None
    primary = next((lnk for lnk in active if lnk.is_primary), active[0])
    return primary.location.code


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
        # Barcode products are picked by EAN, never by photo, so they never need
        # a reference image — treat them as "has image" so they don't fall into
        # the "Wacht op foto's" bucket or prompt a camera capture.
        has_image=len(line.sku.reference_images) > 0 or line.sku.product_type == "barcode",
        is_bottle=line.sku.is_bottle,
        is_item=line.sku.product_type == "barcode",
        pick_location=_primary_location_code(line.sku),
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

    # An order is barcode-picked only when every product on it is a barcode
    # product; anything with a vision product (or an empty order) keeps the
    # camera flow. Drives which scanner the courier UI opens.
    line_product_types = {line.sku.product_type for line in order.lines}
    pick_method = "barcode" if line_product_types == {"barcode"} else "vision"

    return OrderResponse(
        id=order.id,
        reference=order.reference,
        status=order.status,
        channel=order.channel,
        inventory_location=order.inventory_location,
        order_kind=order.order_kind,
        destination_location=order.destination_location,
        pick_method=pick_method,
        remarks=order.remarks or "",
        delivery_week=order.delivery_week,
        allowed_delivery_days=(
            order.lines[0].customer.delivery_days
            if order.lines and order.lines[0].customer is not None
            else []
        ),
        organization_id=order.organization_id,
        organization_name=order.organization.name if order.organization else "",
        created_by_name=order.creator.username if order.creator else "",
        created_at=order.created_at,
        ordered_at=order.ordered_at,
        updated_at=order.updated_at,
        customer_name=order.lines[0].customer_name if order.lines else None,
        lines=lines,
        total_boxes=sum(
            l.quantity
            for l in order.lines
            if l.sku.product_type != "barcode" and not l.sku.is_bottle
        ),
        booked_boxes=sum(
            l.booked_count
            for l in order.lines
            if l.sku.product_type != "barcode" and not l.sku.is_bottle
        ),
        total_bottles=sum(
            l.quantity
            for l in order.lines
            if l.sku.product_type != "barcode" and l.sku.is_bottle
        ),
        booked_bottles=sum(
            l.booked_count
            for l in order.lines
            if l.sku.product_type != "barcode" and l.sku.is_bottle
        ),
        total_items=sum(
            l.quantity for l in order.lines if l.sku.product_type == "barcode"
        ),
        booked_items=sum(
            l.booked_count for l in order.lines if l.sku.product_type == "barcode"
        ),
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


def _is_barcode_only_org(org: Organization | None) -> bool:
    if org is None:
        return False
    return "barcode_picking" in org.modules and "vision_picking" not in org.modules


def _current_iso_week() -> str:
    today = datetime.date.today()
    return f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"


def _week_planning_org_ids(db: Session) -> list[int]:
    """Org ids that run the weekly approval/planning flow (week_overview module).

    Scopes the weekly views' weekless-order fallback: born-active channel orders
    (barcode merchants have no delivery_week and never go through approval) must
    not leak into the wine week views — most visibly the courier's cross-org pick
    carousel. Orgs are few, so loading and filtering in Python keeps the query
    portable across Postgres and the SQLite test DB.
    """
    return [o.id for o in db.query(Organization).all() if "week_overview" in o.modules]


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
    # Barcode-only merchants receive orders through their sales channel and do not
    # have a manual order flow. Other organizations await approval only when they
    # use weekly planning; without it a manual order is born active.
    org = db.get(Organization, org_id)
    if _is_barcode_only_org(org):
        raise HTTPException(
            403,
            "Handmatige orders zijn niet beschikbaar voor EAN-organisaties",
        )
    needs_approval = bool(org and "week_overview" in org.modules)
    order = Order(
        organization_id=org_id,
        created_by=user.id,
        reference=ref,
        channel="manual",
        status="pending_approval" if needs_approval else "active",
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

    customer_name = next(iter(customer_cache.values())).name
    enqueue_customer_order_created(
        db,
        order,
        creator=user,
        customer_name=customer_name,
    )

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


@router.post("/replenishment", response_model=OrderResponse, status_code=201)
def create_replenishment_order(
    body: ReplenishmentOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
):
    """Order stock from the warehouse for the merchant's own shop or webshop.

    Goods arrive at the warehouse in boxes but are sold as loose bottles, so
    this is how a box gets onto the shelf it is sold from. The courier picks it
    like any other order; the booking turns the box into bottles in the chosen
    pool (see ``replenishment_credit``).

    There is no approval step and no delivery week: the merchant is asking for
    their own goods, and nobody else has to agree to that. Nothing is reserved
    either — stock moves when it is actually picked.

    Refuses a box without a bottle link up front. Discovering that in the
    warehouse, with the box already in hand, is the worst moment to find out.
    """
    org_id = _resolve_organization_id(user, body.organization_id, db)
    org = db.get(Organization, org_id)
    if _is_barcode_only_org(org):
        raise HTTPException(
            403,
            "Bevoorradingsorders zijn niet beschikbaar voor EAN-organisaties",
        )

    # Same product on two lines is one line; picking counts units, not rows.
    quantities: dict[int, int] = {}
    for line in body.lines:
        quantities[line.sku_id] = quantities.get(line.sku_id, 0) + line.quantity

    skus: dict[int, SKU] = {}
    for sku_id in quantities:
        sku = db.get(SKU, sku_id)
        if not sku or sku.organization_id != org_id:
            raise HTTPException(404, f"SKU met id {sku_id} niet gevonden")
        if not sku.is_bottle and sku.bottle_sku_id is None:
            raise HTTPException(
                409,
                f"'{sku.name}' is niet aan een fles gekoppeld; "
                "koppel de fles bij het product voordat je hem bestelt",
            )
        skus[sku_id] = sku

    destination_label = LOCATION_LABELS[body.destination_location]
    order = Order(
        organization_id=org_id,
        created_by=user.id,
        reference=f"BVR-{uuid.uuid4().hex[:8].upper()}",
        channel="manual",
        status="active",
        order_kind="replenishment",
        inventory_location="warehouse",
        destination_location=body.destination_location,
        remarks=body.remarks,
        delivery_week=None,
    )
    db.add(order)
    db.flush()

    lines = [
        OrderLine(
            order_id=order.id,
            sku_id=sku_id,
            customer_id=None,
            # No customer, so this is what the courier sees on the pick screen.
            klant=f"Voorraad {destination_label}",
            quantity=quantity,
        )
        for sku_id, quantity in quantities.items()
    ]
    db.add_all(lines)
    db.flush()

    # A vision product without a reference photo cannot be matched by the
    # camera, so the order waits in pending_images exactly like a customer
    # order does — adding the photo promotes it.
    recompute_order_status(order, lines)
    if order.status == "active" and not all(
        len(line.sku.reference_images) > 0 or line.sku.product_type == "barcode"
        for line in lines
    ):
        order.status = "pending_images"

    db.commit()
    db.refresh(order)

    publish_event(
        "order_created_replenishment",
        details={
            "order_reference": order.reference,
            "destination_location": body.destination_location,
            "total_lines": len(quantities),
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return _order_to_response(order, db)


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
                "completed", "shipped", "cancelled", "closed",
            )))
        else:
            # Open work, plus *only* completed channel orders that still need
            # their shipping-label step ("Te verzenden"). Completed manual orders
            # are terminal (no label) and must stay out — otherwise a burst of
            # them would fill the sorted limit=100 window and hide active work.
            # "shipped"/terminal states stay behind include_history.
            query = query.filter(
                or_(
                    Order.status.in_(
                        ("pending_approval", "pending_images", "active")
                    ),
                    and_(
                        Order.status == "completed",
                        Order.channel != "manual",
                        Order.channel_reference.isnot(None),
                    ),
                )
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

    # Inert channel orders belong to the reconciliation view (Fase 3), not the
    # normal order list: "observed" (not live yet), "pending_product" (blocked on a
    # missing catalog entry) and "needs_review" (an active order that gained an
    # unknown line while being picked). The courier filters above already exclude
    # them; this also keeps them out of admin/owner listings.
    query = query.filter(
        Order.status.notin_(("observed", "pending_product", "needs_review"))
    )

    if week:
        # Barcode/channel orders are born active without a delivery week, so they
        # belong to no week at all — surface them alongside the selected week so
        # the courier always sees orders that are ready to pick now.
        query = query.filter(
            or_(Order.delivery_week == week, Order.delivery_week.is_(None))
        )

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
    """Photos for order lines that are not fully picked when this view opens.

    Shared by the courier scan flow ("Deze week" + scan-suggestie-carousel), so
    this must NOT use a user-org module guard: couriers have no organization and
    would always 403. The per-order/per-org method gating for the pick flow is
    handled against the *order's* organization in Fase 1, not here.
    """
    if not week:
        today = datetime.date.today()
        week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"

    monday, sunday = _parse_iso_week(week)
    start_dt = datetime.datetime.combine(monday, datetime.time.min)
    end_dt = datetime.datetime.combine(sunday, datetime.time.max)
    week_org_ids = _week_planning_org_ids(db)

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
                # Weekless fallback only for week-planning orgs: born-active
                # channel orders (no delivery_week) must not surface here.
                and_(
                    Order.delivery_week.is_(None),
                    Order.organization_id.in_(week_org_ids),
                    Order.created_at >= start_dt,
                    Order.created_at <= end_dt,
                ),
                # A replenishment order belongs to nobody's delivery week, but
                # it does have to be picked. Tying it to the week-planning orgs
                # would hide it from every merchant without that module, so it
                # surfaces in the week it was placed regardless.
                and_(
                    Order.order_kind == "replenishment",
                    Order.delivery_week.is_(None),
                    Order.created_at >= start_dt,
                    Order.created_at <= end_dt,
                ),
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
                order_line_ids=sorted(l.id for l in sku_lines),
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


def _monthly_units_by_organization(
    db: Session,
    user: User,
    organization_id: int | None,
    order_kind: str,
) -> list[MonthlyBoxesOrganization]:
    """Booked units per organization per month, for one kind of order.

    Split by kind because the two are different work with different meaning:
    customer orders are volume that left the building, replenishment is the
    merchant's own stock being moved onto a shelf. Adding them up would count the
    same bottles twice — once here and again on the customer order that later
    ships them — so they are reported side by side instead.
    """
    # Per finalized order: its organization, finalize moment and booked boxes.
    #
    # ``finalized_at`` alone decides membership — deliberately *not* the current
    # status. A picked order can still be parked (needs_review) or cancelled by a
    # later channel sync, and reading the live status would then retroactively
    # shrink a month that was already reported as closed. The work was done; the
    # stamp records that. Reverted work is removed at its source instead: undoing
    # a booking clears the stamp when the order reopens, and a cancel-with-restock
    # zeroes ``booked_count``, so both drop out through the units check below.
    query = (
        db.query(
            Order.id.label("order_id"),
            Order.organization_id.label("org_id"),
            Order.finalized_at.label("finalized_at"),
            SKU.is_bottle.label("is_bottle"),
            SKU.product_type.label("product_type"),
            func.coalesce(func.sum(OrderLine.booked_count), 0).label("units"),
            # Only lines that actually went out. A line booked at 0 was not
            # shipped, so it should not show up as a processed order line.
            func.count(case((OrderLine.booked_count > 0, OrderLine.id))).label(
                "lines"
            ),
        )
        .join(OrderLine, OrderLine.order_id == Order.id)
        .join(SKU, OrderLine.sku_id == SKU.id)
        .filter(Order.finalized_at.isnot(None))
        .filter(Order.order_kind == order_kind)
        .group_by(
            Order.id,
            Order.organization_id,
            Order.finalized_at,
            SKU.is_bottle,
            SKU.product_type,
        )
    )

    if user.is_platform_admin or user.role == "courier":
        if organization_id is not None:
            query = query.filter(Order.organization_id == organization_id)
    elif user.organization_id and user.role in ("owner", "member"):
        query = query.filter(Order.organization_id == user.organization_id)
    else:
        raise HTTPException(403, "Geen toegang tot dit overzicht")

    # Bucket booked units into (organization, "YYYY-MM") by handling unit. Orders
    # and lines are tracked separately, and only for barcode rows: a mixed order
    # must not inflate the item counts of a wine-only merchant.
    buckets: dict[int | None, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"boxes": 0, "bottles": 0, "items": 0})
    )
    item_orders: dict[int | None, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    item_lines: dict[int | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in query.all():
        if not row.units or row.finalized_at is None:
            continue
        month = _report_month(row.finalized_at)
        unit = (
            "items"
            if row.product_type == "barcode"
            else "bottles"
            if row.is_bottle
            else "boxes"
        )
        buckets[row.org_id][month][unit] += int(row.units)
        if unit == "items":
            item_orders[row.org_id][month].add(row.order_id)
            item_lines[row.org_id][month] += int(row.lines)

    if not buckets:
        return []

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
        orders_by_month = item_orders[org_id]
        lines_by_month = item_lines[org_id]
        month_rows = [
            MonthlyBoxesMonth(
                month=m,
                boxes=v["boxes"],
                bottles=v["bottles"],
                items=v["items"],
                item_order_count=len(orders_by_month[m]),
                item_line_count=lines_by_month[m],
            )
            for m, v in sorted(months.items(), reverse=True)
        ]
        organizations.append(
            MonthlyBoxesOrganization(
                organization_id=org_id,
                organization_name=org_name,
                total_boxes=sum(v["boxes"] for v in months.values()),
                total_bottles=sum(v["bottles"] for v in months.values()),
                total_items=sum(v["items"] for v in months.values()),
                # An order is finalized once, so it lands in exactly one month:
                # summing per-month counts cannot double-count it.
                total_item_orders=sum(len(o) for o in orders_by_month.values()),
                total_item_lines=sum(lines_by_month.values()),
                months=month_rows,
            )
        )

    organizations.sort(key=lambda o: o.organization_name.lower())
    return organizations


@router.get("/reports/monthly-boxes", response_model=MonthlyBoxesResponse)
def monthly_booked_boxes(
    organization_id: int | None = Query(
        None, description="Optioneel: beperk tot één handelaar."
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Aantal verwerkte eenheden per maand voor afgeronde orders.

    Eenheden tellen mee in de maand waarin de order is afgerond (``finalized_at``)
    en worden uitgesplitst als dozen, flessen of items. Het aantal is het werkelijk
    geboekte aantal. Gegroepeerd per handelaar (organisatie).

    Voor barcode-producten tellen we daarnaast het aantal orders en orderregels:
    daar zegt het aantal stuks weinig over de verwerkte hoeveelheid werk. Dozen en
    flessen blijven puur op eenheden geteld.

    Bevoorradingsorders staan apart in ``replenishment``. Het is echt verwerkt
    werk — de koerier pickt die dozen — maar de flessen verlaten het pand niet en
    komen later nog een keer langs op de klantorder die ze verscheept. Bij elkaar
    optellen zou diezelfde flessen dus dubbel tellen.

    Zichtbaar voor de koerier en platform-admin (alle handelaren) en voor een
    owner/member (alleen de eigen handelaar).
    """
    return MonthlyBoxesResponse(
        organizations=_monthly_units_by_organization(
            db, user, organization_id, "customer"
        ),
        replenishment=_monthly_units_by_organization(
            db, user, organization_id, "replenishment"
        ),
    )


@router.get("/weekly-summary", response_model=WeeklySummaryResponse)
def weekly_order_summary(
    week: str = Query(None, description="ISO week, bijv. '2026-W15'. Standaard: huidige week."),
    group_by: str = Query("supplier", description="Groepering: 'supplier' of 'customer'."),
    db: Session = Depends(get_db),
    user: User = Depends(require_merchant),
    _wk: User = Depends(require_module("week_overview")),
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
    week_org_ids = _week_planning_org_ids(db)

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
            # Keep approved orders visible after fulfilment so a selected week
            # remains a useful historical overview. Cancelled and unapproved
            # orders deliberately stay out.
            Order.status.in_(("pending_images", "active", "completed", "closed")),
            # A replenishment order is the merchant restocking their own shelf,
            # not a customer buying something. Counting it here would inflate
            # both the supplier order quantities and the customer totals with
            # goods that never left the building.
            Order.order_kind == "customer",
            or_(
                Order.delivery_week == week,
                # Weekless fallback only for week-planning orgs (legacy wine
                # orders); born-active channel orders must stay out.
                and_(
                    Order.delivery_week.is_(None),
                    Order.organization_id.in_(week_org_ids),
                    Order.created_at >= start_dt,
                    Order.created_at <= end_dt,
                ),
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

    # Physical stock is live data. Load it in one query for every product and
    # organization in the selected week; box stock does not use reservations.
    inventory_keys = {
        (line.order.organization_id, line.sku_id)
        for line in lines
    }
    inventory_by_key: dict[tuple[int | None, int], int] = {}
    if inventory_keys:
        sku_ids = {sku_id for _, sku_id in inventory_keys}
        organization_ids = {organization_id for organization_id, _ in inventory_keys}
        balances = (
            db.query(InventoryBalance)
            .filter(
                InventoryBalance.sku_id.in_(sku_ids),
                InventoryBalance.organization_id.in_(organization_ids),
                InventoryBalance.inventory_location == "warehouse",
            )
            .all()
        )
        inventory_by_key = {
            (balance.organization_id, balance.sku_id): balance.quantity_on_hand
            for balance in balances
        }

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
            "order_id": line.order_id,
            "order_status": line.order.status,
            "sku": sku,
            "quantity": line.quantity,
            "current_stock": inventory_by_key.get(
                (line.order.organization_id, line.sku_id), 0
            ),
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
            order_status_by_id = {
                e["order_id"]: e["order_status"]
                for e in entries
            }
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
                current_stock=entries[0]["current_stock"],
                completed_order_count=sum(
                    status == "completed" for status in order_status_by_id.values()
                ),
                closed_order_count=sum(
                    status == "closed" for status in order_status_by_id.values()
                ),
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


def _next_pick_sort_key(line: OrderLine, order: Order, context_order_id: int):
    """Same ordering as receiving._select_order_line_for_scope.

    Prefer the started/context order, then fall back to week FIFO, delivery
    day, and line id — so opening an order shows one of its own products first
    while the suggested line still matches where book_box will book the scan.
    """
    return (
        0 if order.id == context_order_id else 1,
        order.delivery_week or "",
        _DELIVERY_DAY_SORT.get(line.delivery_day, 9),
        line.id,
    )


@router.get("/{order_id}/next-pick", response_model=NextPickResponse | None)
def next_pick(
    order_id: int,
    scan_mode: str = Query("box", description="'box' of 'bottle' — selecteert de eenheid."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Suggestion photo for the next SKU to scan, for the given scan mode.

    Selects the line book_box would actually book, using the same scope and
    ordering as receiving (the started/context order first, then week FIFO as
    fallback). This keeps the first suggestion tied to the order the picker
    opened without letting the card and booking destination disagree.

    Scope mirrors receiving._open_scope_lines_query: a scheduled context order
    sweeps every active scheduled order in the org across all weeks; an ad-hoc
    order (no delivery_week) stays scoped to itself. No status gate on the
    context order: we want a suggestion exactly when it has just been completed.
    """
    bottle = scan_mode == "bottle"
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

    if order.delivery_week:
        scope_orders = (
            db.query(Order)
            .filter(
                Order.status == "active",
                Order.organization_id == order.organization_id,
                Order.delivery_week.isnot(None),
            )
            .options(*pick_options)
            .all()
        )
    else:
        scope_orders = [order]

    candidates = [
        (line, o)
        for o in scope_orders
        for line in o.lines
        if line.booked_count < line.quantity and line.sku.is_bottle == bottle
    ]
    if not candidates:
        return None

    line, o = min(
        candidates,
        key=lambda item: _next_pick_sort_key(item[0], item[1], order.id),
    )
    source = "this_order" if o.id == order.id else "other_order"
    return _to_next_pick(line, o, source)


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
    _wk: User = Depends(require_module("week_overview")),
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

    previous_status = order.status

    week = body.week if body else None
    if week:
        _parse_iso_week(week)  # validates the format, raises 400 otherwise

    delivery_day = body.delivery_day if body else None
    if delivery_day and order.status != "pending_approval":
        raise HTTPException(
            400, "Leverdag kan alleen bij de eerste goedkeuring worden aangepast"
        )
    if delivery_day:
        # One order always belongs to one customer. Validate every line before
        # mutating any of them, then apply the chosen day to the whole order.
        for line in order.lines:
            if line.customer is None:
                raise HTTPException(400, "Klant bij orderregel ontbreekt")
            _check_delivery_day_allowed(delivery_day, line.customer)
        for line in order.lines:
            line.delivery_day = delivery_day

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

    if previous_status != "active" and order.status == "active":
        enqueue_approved_order_ready(db, order)

    db.commit()
    db.refresh(order)

    publish_event(
        "order_approved",
        details={
            "order_reference": order.reference,
            "new_status": order.status,
            "delivery_day": delivery_day,
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
    background_tasks: BackgroundTasks,
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

    # Lock only after the access check: a caller who may not touch this order
    # must not be able to hold its line locks — and stall the courier's scans —
    # for the length of their request. Within the lock, match the order used by
    # apply_booking (lines first, then the order) so a concurrent scan cannot
    # change booked_count while the remaining reservation is calculated.
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order_id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    db.refresh(order, with_for_update=True)

    if order.status not in ("active", "pending_images"):
        raise HTTPException(400, f"Order kan niet gesloten worden (status: {order.status})")

    released_reserved_boxes = 0
    released_sku_ids: list[int] = []
    if (
        order.status == "active"
        and order.channel != "manual"
        and order.organization_id is not None
    ):
        remaining_by_sku: dict[int, int] = defaultdict(int)
        for line in lines:
            remaining_by_sku[line.sku_id] += remaining_for_line(line)
        for sku_id, remaining in sorted(remaining_by_sku.items()):
            if remaining <= 0:
                continue
            applied = adjust_reservation(
                db,
                sku_id=sku_id,
                organization_id=order.organization_id,
                delta=-remaining,
            )
            if applied != -remaining:
                # The reservation is a shared per-product counter, clamped at 0.
                # Releasing more than is reserved means this order held less than
                # its open lines suggest (activated before reservations existed,
                # or already released elsewhere) — the shortfall was taken from
                # what other open orders reserved, so say so instead of silently
                # freeing their stock.
                logger.warning(
                    "close_order: reservering geklemd voor SKU %s (order %s): "
                    "%s gevraagd, %s vrijgegeven",
                    sku_id,
                    order.reference,
                    remaining,
                    -applied,
                )
            if applied:
                released_reserved_boxes += -applied
                released_sku_ids.append(sku_id)

    order.status = "closed"
    order.mark_finalized()
    db.commit()
    db.refresh(order)

    # Releasing a reservation raises the available number a sales channel should
    # see. Mirror it outward after the response, like every other reservation or
    # stock write (see picking.scan_ean, channels.resolve_channel_review).
    for sku_id in released_sku_ids:
        background_tasks.add_task(
            push_inventory_to_channels, sku_id, order.organization_id
        )

    publish_event(
        "order_closed",
        details={
            "order_reference": order.reference,
            "total_boxes": sum(line.quantity for line in lines),
            "booked_boxes": sum(line.booked_count for line in lines),
            "released_reserved_boxes": released_reserved_boxes,
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
    """Recompute order status from the order's current lines.

    Thin wrapper over the shared service helper; callers here operate on the
    in-session ``order.lines`` after their own edits.
    """
    recompute_order_status(order, order.lines)


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
