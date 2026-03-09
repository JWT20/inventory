"""Order management: CSV upload, SKU validation, order lifecycle."""

import csv
import io
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin, require_product_manager
from app.database import get_db
from app.events import publish_event
from app.models import Booking, Order, OrderLine, SKU, User
from app.schemas import (
    BookingResponse,
    CSVRow,
    CSVValidationResult,
    ManualOrderCreate,
    OrderLineResponse,
    OrderResponse,
    SKUResponse,
    generate_display_name,
    generate_sku_code,
)
from app.routers.skus import _sku_to_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


REQUIRED_CSV_COLUMNS = {"klant", "producent", "wijnaam", "type", "jaargang", "volume", "aantal"}


def _order_line_to_response(line: OrderLine) -> OrderLineResponse:
    return OrderLineResponse(
        id=line.id,
        sku_id=line.sku_id,
        sku_code=line.sku.sku_code,
        sku_name=line.sku.name,
        klant=line.klant,
        quantity=line.quantity,
        booked_count=line.booked_count,
        has_image=len(line.sku.reference_images) > 0,
    )


def _order_to_response(order: Order) -> OrderResponse:
    lines = [_order_line_to_response(l) for l in order.lines]
    return OrderResponse(
        id=order.id,
        reference=order.reference,
        status=order.status,
        merchant_name=order.merchant.username,
        created_at=order.created_at,
        updated_at=order.updated_at,
        lines=lines,
        total_boxes=sum(l.quantity for l in order.lines),
        booked_boxes=sum(l.booked_count for l in order.lines),
    )


def _parse_csv(content: str) -> tuple[list[CSVRow], list[str]]:
    """Parse CSV content and return rows + errors."""
    errors: list[str] = []
    rows: list[CSVRow] = []

    reader = csv.DictReader(io.StringIO(content), delimiter=";")

    if reader.fieldnames is None:
        return [], ["CSV bestand is leeg of ongeldig"]

    # Normalize headers (lowercase, strip)
    normalized = {h.strip().lower(): h for h in reader.fieldnames}
    missing = REQUIRED_CSV_COLUMNS - set(normalized.keys())
    if missing:
        return [], [f"Ontbrekende kolommen: {', '.join(sorted(missing))}"]

    for i, raw_row in enumerate(reader, start=2):
        # Map normalized keys back
        row_data = {k: raw_row[normalized[k]].strip() for k in normalized if normalized[k] in raw_row}
        try:
            aantal = int(row_data.get("aantal", "0"))
            if aantal <= 0:
                errors.append(f"Rij {i}: aantal moet groter zijn dan 0")
                continue
            csv_row = CSVRow(
                klant=row_data["klant"],
                producent=row_data["producent"],
                wijnaam=row_data["wijnaam"],
                type=row_data["type"],
                jaargang=row_data["jaargang"],
                volume=row_data["volume"],
                aantal=aantal,
            )
            rows.append(csv_row)
        except (ValueError, KeyError) as e:
            errors.append(f"Rij {i}: {e}")

    return rows, errors


@router.post("/upload-csv", response_model=CSVValidationResult)
def upload_csv(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Upload CSV, validate SKUs, create/match SKUs, create draft order."""
    if not file.filename or not (file.filename.endswith(".csv") or file.filename.endswith(".txt")):
        raise HTTPException(400, "Alleen CSV bestanden zijn toegestaan")

    raw = file.file.read()
    try:
        content = raw.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        content = raw.decode("latin-1")

    rows, errors = _parse_csv(content)
    if not rows and errors:
        raise HTTPException(400, detail="; ".join(errors))

    matched_skus: list[SKU] = []
    new_skus: list[SKU] = []
    seen_codes: set[str] = set()

    for row in rows:
        sku_code = row.sku_code
        if sku_code in seen_codes:
            continue
        seen_codes.add(sku_code)

        existing = db.query(SKU).filter(SKU.sku_code == sku_code).first()
        if existing:
            matched_skus.append(existing)
        else:
            sku = SKU(
                sku_code=sku_code,
                name=row.display_name,
                description=f"{row.producent} {row.wijnaam} {row.type} {row.jaargang} {row.volume}",
                producent=row.producent,
                wijnaam=row.wijnaam,
                wijntype=row.type,
                jaargang=row.jaargang,
                volume=row.volume,
            )
            db.add(sku)
            db.flush()
            new_skus.append(sku)

    # Create order with lines
    ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = Order(merchant_id=user.id, reference=ref, status="draft")
    db.add(order)
    db.flush()

    # Group rows by (SKU code, klant) and sum quantities
    line_quantities: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.sku_code, row.klant)
        line_quantities[key] = line_quantities.get(key, 0) + row.aantal

    all_skus = {s.sku_code: s for s in matched_skus + new_skus}
    for (sku_code, klant), qty in line_quantities.items():
        sku = all_skus[sku_code]
        line = OrderLine(order_id=order.id, sku_id=sku.id, klant=klant, quantity=qty)
        db.add(line)

    # Determine status: if any new SKU has no image → pending_images
    if new_skus:
        order.status = "pending_images"
    else:
        all_have_images = all(len(s.reference_images) > 0 for s in matched_skus)
        order.status = "active" if all_have_images else "pending_images"

    db.commit()

    publish_event(
        "order_created_from_csv",
        details={
            "order_reference": ref,
            "matched_count": len(matched_skus),
            "new_count": len(new_skus),
            "total_lines": len(line_quantities),
        },
        user=user,
        resource_type="order",
        resource_id=order.id,
    )

    return CSVValidationResult(
        matched_skus=[_sku_to_response(s) for s in matched_skus],
        new_skus=[_sku_to_response(s) for s in new_skus],
        errors=errors,
    )


@router.post("", response_model=OrderResponse)
def create_order(
    body: ManualOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Create an order from wine details. SKUs are auto-created or matched."""
    merchant = db.get(User, body.merchant_id)
    if not merchant:
        raise HTTPException(404, "Handelaar niet gevonden")

    ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = Order(merchant_id=merchant.id, reference=ref, status="draft")
    db.add(order)
    db.flush()

    # Group by (sku_code, klant), sum quantities, resolve SKUs
    line_key = tuple[str, str]  # (sku_code, klant)
    line_quantities: dict[line_key, int] = {}
    line_data: dict[str, object] = {}  # sku_code → first line (for SKU creation)
    for line in body.lines:
        code = generate_sku_code(line.producent, line.wijnaam, line.wijntype, line.jaargang, line.volume)
        key = (code, line.klant)
        line_quantities[key] = line_quantities.get(key, 0) + line.quantity
        if code not in line_data:
            line_data[code] = line

    has_new = False
    for (code, klant), qty in line_quantities.items():
        line = line_data[code]
        sku = db.query(SKU).filter(SKU.sku_code == code).first()
        if not sku:
            sku = SKU(
                sku_code=code,
                name=generate_display_name(line.producent, line.wijnaam, line.wijntype, line.jaargang),
                description=f"{line.producent} {line.wijnaam} {line.wijntype} {line.jaargang} {line.volume}",
                producent=line.producent,
                wijnaam=line.wijnaam,
                wijntype=line.wijntype,
                jaargang=line.jaargang,
                volume=line.volume,
            )
            db.add(sku)
            db.flush()
            has_new = True
        db.add(OrderLine(order_id=order.id, sku_id=sku.id, klant=klant, quantity=qty))

    # Determine status
    if has_new:
        order.status = "pending_images"
    else:
        all_skus = [db.query(SKU).filter(SKU.sku_code == c).first() for c in line_data]
        all_have_images = all(len(s.reference_images) > 0 for s in all_skus)
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

    return _order_to_response(order)


@router.get("", response_model=list[OrderResponse])
def list_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List orders. Merchants see their own, admins see all."""
    query = db.query(Order)
    if user.role == "merchant":
        query = query.filter(Order.merchant_id == user.id)
    orders = query.order_by(Order.created_at.desc()).all()
    return [_order_to_response(o) for o in orders]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
    return _order_to_response(order)


@router.post("/{order_id}/activate", response_model=OrderResponse)
def activate_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Activate order — only possible when all SKUs have reference images."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")
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

    return _order_to_response(order)


@router.delete("/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Delete an order and all its lines and bookings (admin only)."""
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
    _: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order niet gevonden")

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
            klant=b.order_line.klant,
            rolcontainer=f"KLANT {b.order_line.klant.upper()}",
            created_at=b.created_at,
        )
        for b in bookings
    ]
