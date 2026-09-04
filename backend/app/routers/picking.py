"""Barcode/EAN order picking — the handscanner counterpart to vision receiving.

Where ``/receiving`` identifies boxes by photo + AI, this books an order line by
a scanned EAN: one scan = one unit. It deliberately shares the vision flow's
machinery rather than forking a second model:

- the same write path (``services.booking.apply_booking``), so stock and order
  status stay consistent with vision bookings;
- the same per-order module guard (``assert_order_module``), keyed on the
  *order's* organization — couriers have no org of their own.

Gated on the order's ``barcode_picking`` module. Scoped to the selected order
(the courier picks one order at a time), unlike the week-wide vision scope.
"""
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import assert_order_module, require_inbound_booker
from app.database import get_db
from app.events import publish_event
from app.models import Booking, Location, Order, OrderLine, SKU, SKULocation, User
from app.services.inventory_sync import push_inventory_to_channels
from app.services.fulfillment_sync import (
    ShopifyFulfillmentError,
    fulfill_shopify_order,
)
from app.services.veloyd import (
    VeloydError,
    VeloydLabelMismatch,
    client_for_organization,
    client_for_user,
    verify_veloyd_label,
)
from app.schemas import (
    EanScanRequest,
    EanScanResponse,
    LabelOrderOpenRequest,
    LabelOrderOpenResponse,
    LabelScanRequest,
    LabelScanResponse,
    LocationScanRequest,
    LocationScanResponse,
    LocationScanSKU,
    UndoScanRequest,
    UndoScanResponse,
)
from app.services.booking import apply_booking, undo_booking

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/picking", tags=["picking"], dependencies=[Depends(require_inbound_booker)]
)


def _can_access_order(user: User, order: Order) -> bool:
    """Mirror the order-scoped warehouse access used by the scan endpoints."""
    if user.is_platform_admin or user.role == "courier":
        return True
    if user.role in ("owner", "member"):
        return order.organization_id == user.organization_id
    return False


def _normalize_tracking_code(value: str) -> str:
    """Stable key for scanner and Veloyd variants of one physical barcode."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_barcode_order(order: Order) -> bool:
    return bool(order.lines) and all(
        line.sku.product_type == "barcode" for line in order.lines
    )


def _assert_openable_label_order(order: Order, user: User) -> None:
    if not _can_access_order(user, order):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    if order.status not in ("active", "completed"):
        raise HTTPException(409, f"Order kan niet worden geopend (status: {order.status})")
    if order.channel == "manual" or not order.channel_reference:
        raise HTTPException(409, "Label hoort niet bij een kanaalorder")
    if not _is_barcode_order(order):
        raise HTTPException(409, "Label hoort niet bij een EAN-order")
    assert_order_module(order, "barcode_picking", user)


def _find_open_orders_by_channel_reference(
    db: Session,
    user: User,
    channel_reference: str,
    *,
    require_unlinked: bool,
) -> list[Order]:
    reference = channel_reference.strip().lstrip("#").strip()
    if not reference:
        return []

    query = (
        db.query(Order)
        .options(
            joinedload(Order.organization),
            joinedload(Order.lines).joinedload(OrderLine.sku),
        )
        .filter(
            Order.channel_reference == reference,
            Order.status.in_(("active", "completed")),
            Order.channel != "manual",
        )
    )
    if not user.is_platform_admin and user.role in ("owner", "member"):
        query = query.filter(Order.organization_id == user.organization_id)

    return [
        order
        for order in query.all()
        if (not require_unlinked or order.veloyd_tracking_code is None)
        and _is_barcode_order(order)
        and order.organization
        and "barcode_picking" in order.organization.modules
    ]


def _require_unambiguous_label_order(candidates: list[Order]) -> Order | None:
    if len(candidates) > 1:
        raise HTTPException(
            409,
            "Meerdere open orders hebben dit ordernummer; kies de order handmatig",
        )
    return candidates[0] if candidates else None


@router.post("/open-by-label", response_model=LabelOrderOpenResponse)
def open_order_by_label(
    body: LabelOrderOpenRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Open an EAN order by scanning its loose Veloyd shipping label.

    Veloyd labels may encode either the visible channel order number or a
    track-and-trace value. A unique channel number resolves locally. Known
    tracking codes also resolve locally; new tracking codes are resolved through
    Veloyd and persisted for subsequent scans.
    """
    scanned_code = body.label_reference.strip()
    normalized_scanned = _normalize_tracking_code(scanned_code)
    if not normalized_scanned:
        raise HTTPException(400, "Geen Veloyd-label gescand")

    known = (
        db.query(Order)
        .filter(Order.veloyd_tracking_code == normalized_scanned)
        .first()
    )
    if known:
        _assert_openable_label_order(known, user)
        return LabelOrderOpenResponse(
            order_id=known.id,
            tracking_code=known.veloyd_tracking_code,
        )

    direct_match = _require_unambiguous_label_order(
        _find_open_orders_by_channel_reference(
            db,
            user,
            scanned_code,
            require_unlinked=False,
        )
    )
    if direct_match:
        return LabelOrderOpenResponse(
            order_id=direct_match.id,
            tracking_code=normalized_scanned,
        )

    try:
        veloyd_label = client_for_user(db, user).parcel_by_tracking_number(
            scanned_code
        )
    except VeloydLabelMismatch as exc:
        raise HTTPException(404, "Geen order gevonden voor dit Veloyd-label") from exc
    except VeloydError as exc:
        raise HTTPException(502, str(exc)) from exc

    tracking_code = _normalize_tracking_code(veloyd_label.tracking_number)
    if not tracking_code:
        raise HTTPException(502, "Veloyd gaf een ongeldige trackingcode terug")
    # Veloyd may return a canonical tracking value that differs in casing or
    # formatting from the scanner input. Check that value before matching anew.
    known = (
        db.query(Order)
        .filter(Order.veloyd_tracking_code == tracking_code)
        .first()
    )
    if known:
        _assert_openable_label_order(known, user)
        return LabelOrderOpenResponse(order_id=known.id, tracking_code=tracking_code)

    # An order already linked to a different physical label cannot be the match.
    # This also disambiguates equal Shopify order numbers after either label has
    # been learned once.
    order = _require_unambiguous_label_order(
        _find_open_orders_by_channel_reference(
            db,
            user,
            veloyd_label.reference,
            require_unlinked=True,
        )
    )
    if not order:
        raise HTTPException(404, "Geen actieve EAN-order gevonden voor dit label")

    order.veloyd_tracking_code = tracking_code
    try:
        # Flush first so the unique index is checked before we report success.
        # A concurrent scan of the same label may win this race.
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        linked = (
            db.query(Order)
            .filter(Order.veloyd_tracking_code == tracking_code)
            .first()
        )
        if linked and linked.id == order.id:
            _assert_openable_label_order(linked, user)
            return LabelOrderOpenResponse(
                order_id=linked.id,
                tracking_code=tracking_code,
            )
        raise HTTPException(
            409, "Dit Veloyd-label is al aan een andere order gekoppeld"
        ) from exc

    publish_event(
        "order_opened_by_label",
        details={
            "order_reference": order.reference,
            "channel_reference": order.channel_reference,
            "pick_method": "barcode",
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return LabelOrderOpenResponse(order_id=order.id, tracking_code=tracking_code)


@router.post("/scan-ean", response_model=EanScanResponse)
def scan_ean(
    body: EanScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Book one unit on a barcode order by its scanned EAN.

    Looks the EAN up within the order's organization (EAN is unique per org),
    finds the matching open order line, and books a single unit through the
    shared booking service.
    """
    order = db.get(Order, body.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    # Owners/members may only act on their own organization's orders; couriers
    # and platform admins serve across organizations. assert_order_module checks
    # the order's *module*, not the caller's *access* — so this guard is what
    # keeps an owner of org A out of org B's order. Same check as receiving.
    if (
        not user.is_platform_admin
        and user.role in ("owner", "member")
        and order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")
    assert_order_module(order, "barcode_picking", user)

    ean = body.ean.strip()
    if not ean:
        raise HTTPException(400, "Geen EAN gescand")

    # EAN is unique per organization (uq_skus_org_ean); resolve within the
    # order's org so two merchants stocking the same EAN never cross.
    sku = (
        db.query(SKU)
        .filter(SKU.organization_id == order.organization_id, SKU.ean == ean)
        .first()
    )
    if not sku:
        raise HTTPException(404, f"Geen product met EAN {ean} in deze organisatie")

    # Location gate (hufterproef): a product that has pick locations may only be
    # booked after its shelf was scanned, and only from a shelf it actually lives
    # on. Products without locations (unplaced, or legacy) skip this entirely so
    # existing barcode orders keep working.
    sku_location_codes = {
        link.location.code
        for link in sku.location_links
        if link.location and link.location.active
    }
    if sku_location_codes:
        scanned = (body.location_code or "").strip()
        if not scanned:
            raise HTTPException(400, f"Scan eerst de locatie voor {sku.name}")
        if scanned not in sku_location_codes:
            raise HTTPException(
                400,
                f"{sku.name} ligt niet op locatie {scanned} "
                f"(hoort op {', '.join(sorted(sku_location_codes))})",
            )

    line = (
        db.query(OrderLine)
        .filter(
            OrderLine.order_id == order.id,
            OrderLine.sku_id == sku.id,
            OrderLine.booked_count < OrderLine.quantity,
        )
        .first()
    )
    if not line:
        # Distinguish "not on this order" from "already complete" so the courier
        # knows whether they grabbed the wrong box or simply finished it.
        on_order = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order.id, OrderLine.sku_id == sku.id)
            .first()
        )
        if on_order:
            raise HTTPException(409, f"{sku.name} is al volledig geboekt op deze order")
        raise HTTPException(400, f"{sku.name} staat niet op deze order")

    result = apply_booking(
        db,
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku.id,
        quantity=1,
        cap_remaining=None,
        scanned_by=user.id,
        scan_image_path=None,
        confidence=None,
    )

    rolcontainer = f"KLANT {line.customer_name.upper()}"
    publish_event(
        "box_booked",
        details={
            "order_reference": order.reference,
            "sku_code": sku.sku_code,
            "ean": ean,
            "rolcontainer": rolcontainer,
            "klant": line.customer_name,
            "order_completed": result.order_completed,
            "quantity": result.booked_quantity,
            "pick_method": "barcode",
        },
        user=user,
        resource_type="booking",
        resource_id=result.last_booking_id,
    )

    # Mirror the new available to every live channel after the response.
    background_tasks.add_task(push_inventory_to_channels, sku.id, order.organization_id)

    return EanScanResponse(
        order_id=order.id,
        order_line_id=line.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        klant=line.customer_name,
        rolcontainer=rolcontainer,
        booked_quantity=result.booked_quantity,
        remaining_quantity=result.remaining,
        order_completed=result.order_completed,
        booking_id=result.last_booking_id,
    )


@router.post("/undo", response_model=UndoScanResponse)
def undo_scan(
    body: UndoScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Undo one previously scanned unit (wrong/damaged item) on a barcode order.

    Resolves the order via the booking, applies the same access + module guard as
    scanning, then reverses the unit through the shared booking service.
    """
    booking = db.get(Booking, body.booking_id)
    if not booking:
        raise HTTPException(404, "Boeking niet gevonden")
    order = db.get(Order, booking.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if (
        not user.is_platform_admin
        and user.role in ("owner", "member")
        and order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    assert_order_module(order, "barcode_picking", user)

    result = undo_booking(db, booking_id=booking.id, performed_by=user.id)

    # Mirror the restored available to all live channels after the response.
    background_tasks.add_task(
        push_inventory_to_channels, result.sku_id, order.organization_id
    )

    publish_event(
        "booking_undone",
        details={
            "order_reference": order.reference,
            "sku_id": result.sku_id,
            "order_status": result.order_status,
            "pick_method": "barcode",
        },
        user=user,
        resource_type="booking",
        resource_id=body.booking_id,
    )

    return UndoScanResponse(
        order_id=result.order_id,
        order_line_id=result.order_line_id,
        sku_id=result.sku_id,
        remaining_quantity=result.remaining,
        order_status=result.order_status,
    )


@router.post("/scan-location", response_model=LocationScanResponse)
def scan_location(
    body: LocationScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Verify a scanned shelf code and return this order's products that live there.

    The pick flow scans a location before its EANs; this confirms the code exists
    and that the order actually has open products on that shelf, so the courier
    walks a real route instead of scanning blind.
    """
    order = db.get(Order, body.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if (
        not user.is_platform_admin
        and user.role in ("owner", "member")
        and order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    if order.status != "active":
        raise HTTPException(400, f"Order is niet actief (status: {order.status})")
    assert_order_module(order, "barcode_picking", user)

    code = body.location_code.strip()
    location = (
        db.query(Location)
        .filter(Location.code == code, Location.active.is_(True))
        .first()
    )
    if not location:
        raise HTTPException(404, f"Onbekende locatie {code}")

    # Order lines whose SKU is placed on this location.
    lines = (
        db.query(OrderLine)
        .join(SKULocation, SKULocation.sku_id == OrderLine.sku_id)
        .filter(
            OrderLine.order_id == order.id,
            SKULocation.location_id == location.id,
        )
        .all()
    )
    if not lines:
        raise HTTPException(
            400, f"Geen producten van deze order op locatie {code}"
        )

    open_lines = [l for l in lines if l.booked_count < l.quantity]
    if not open_lines:
        raise HTTPException(
            409, f"Alles op locatie {code} is al gepickt"
        )

    return LocationScanResponse(
        order_id=order.id,
        location_code=code,
        skus=[
            LocationScanSKU(
                sku_id=l.sku_id,
                sku_code=l.sku.sku_code,
                sku_name=l.sku.name,
                ean=l.sku.ean,
                remaining_quantity=l.quantity - l.booked_count,
            )
            for l in open_lines
        ],
    )


@router.post("/scan-label", response_model=LabelScanResponse)
def scan_label(
    body: LabelScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_inbound_booker),
):
    """Shipping-label verification gate: the final step on a barcode order.

    Veloyd prints the channel order number (``channel_reference``) on the label.
    A fully picked order (status ``completed``) is only released to ``shipped``
    when the scanned label matches that reference — this is what stops the right
    box leaving with the wrong customer's label.
    """
    order = db.get(Order, body.order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    if (
        not user.is_platform_admin
        and user.role in ("owner", "member")
        and order.organization_id != user.organization_id
    ):
        raise HTTPException(403, "Geen toegang tot deze organisatie")
    assert_order_module(order, "barcode_picking", user)

    # Lock the order before checking status: without this a concurrent undo can
    # reopen the order between the check and the ``shipped`` write, shipping an
    # order that is no longer fully picked.
    db.refresh(order, with_for_update=True)

    if order.status == "shipped":
        raise HTTPException(409, "Order is al verzonden")
    if order.status != "completed":
        raise HTTPException(400, "Order is nog niet volledig gepickt")
    if not order.channel_reference:
        # Should not happen — channel orders always carry a reference. Fail loud
        # rather than silently shipping an unverifiable order.
        raise HTTPException(409, "Order mist een kanaalreferentie voor labelmatch")

    # The label may carry a leading '#'; channel_reference is stored normalized
    # without it (see Order.channel_reference).
    label = body.label_reference.strip().lstrip("#")
    normalized_label = _normalize_tracking_code(label)
    if (
        order.veloyd_tracking_code
        and normalized_label != order.veloyd_tracking_code
    ):
        # The first scan selected and linked one physical label. Requiring that
        # exact same barcode here prevents another label for an equal order number
        # from being placed on the packed order.
        raise HTTPException(409, "Scan hetzelfde Veloyd-label als bij het openen")
    tracking_info = None
    if label != order.channel_reference:
        try:
            veloyd_label = verify_veloyd_label(
                label,
                order.channel_reference,
                client=client_for_organization(db, order.organization_id),
            )
            tracking_info = veloyd_label.shopify_tracking_info
        except VeloydLabelMismatch as exc:
            raise HTTPException(409, str(exc)) from exc
        except VeloydError as exc:
            raise HTTPException(502, str(exc)) from exc

        # Backfill legacy orders that reached the final scan without first being
        # opened through the new loose-label flow.
        if not order.veloyd_tracking_code:
            tracking_code = _normalize_tracking_code(veloyd_label.tracking_number)
            if not tracking_code:
                raise HTTPException(502, "Veloyd gaf een ongeldige trackingcode terug")
            conflict = (
                db.query(Order.id)
                .filter(
                    Order.veloyd_tracking_code == tracking_code,
                    Order.id != order.id,
                )
                .first()
            )
            if conflict:
                raise HTTPException(
                    409, "Dit Veloyd-label is al aan een andere order gekoppeld"
                )
            order.veloyd_tracking_code = tracking_code
            try:
                # Force the unique check before Shopify is called. PostgreSQL
                # waits for a concurrent writer here, making this race-safe too.
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(
                    409, "Dit Veloyd-label is al aan een andere order gekoppeld"
                ) from exc

    if order.channel == "shopify":
        # Shopify is written by this app. Do this before the local transition so
        # a missing scope or outage leaves the order safely retryable.
        try:
            fulfill_shopify_order(db, order, tracking_info=tracking_info)
        except ShopifyFulfillmentError as exc:
            db.rollback()
            raise HTTPException(502, str(exc)) from exc
    elif order.channel == "bol":
        # Veloyd owns bol shipment confirmation + T&T. Calling bol here too
        # would create two writers for the same shipment. The verified physical
        # label is enough to close the local warehouse order; the next bol sync
        # imports Veloyd's external fulfillment state.
        pass
    else:
        raise HTTPException(409, "Order heeft geen ondersteund verkoopkanaal")

    order.status = "shipped"
    order.mark_finalized()  # idempotent: already stamped at completion
    db.commit()

    publish_event(
        "order_shipped",
        details={
            "order_reference": order.reference,
            "channel_reference": order.channel_reference,
            "pick_method": "barcode",
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return LabelScanResponse(
        order_id=order.id,
        status=order.status,
        reference=order.channel_reference,
    )
