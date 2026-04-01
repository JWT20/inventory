import logging

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, require_product_manager, require_warehouse
from app.database import get_db
from app.events import publish_event
from app.models import (
    SKU,
    SKUAttribute,
    Customer,
    CustomerSKU,
    InboundShipment,
    InboundShipmentLine,
    InventoryBalance,
    ReferenceImage,
    SupplierSKUMapping,
    StockMovement,
    User,
)
from app.schemas import (
    CustomerPriceResponse,
    InventoryAdjustRequest,
    InventoryBalanceResponse,
    InventoryCountRequest,
    InventoryOverviewItem,
    ShipmentCreate,
    ShipmentExtractPreviewResponse,
    ShipmentExtractedLine,
    ShipmentLineResponse,
    ShipmentResponse,
    StockMovementResponse,
    UpdateCustomerPriceRequest,
    UpdateCustomerSKUDiscountRequest,
    UpdateDefaultPriceRequest,
)
from app.services.embedding import extract_shipment_document
from app.services.storage import storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inventory"])


# ---------------------------------------------------------------------------
# Shared helper — used by this router AND by receiving.py
# ---------------------------------------------------------------------------

def apply_stock_movement(
    db: Session,
    *,
    sku_id: int,
    organization_id: int,
    quantity: int,
    movement_type: str,
    reference_type: str | None = None,
    reference_id: int | None = None,
    note: str | None = None,
    performed_by: int,
) -> StockMovement:
    """Create a stock movement and update the inventory balance.

    Does NOT commit — the caller controls the transaction boundary.
    Raises HTTPException(409) if the resulting balance would go negative.
    """
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == organization_id,
        )
        .with_for_update()
        .first()
    )
    if not balance:
        balance = InventoryBalance(
            sku_id=sku_id,
            organization_id=organization_id,
            quantity_on_hand=0,
        )
        db.add(balance)
        db.flush()

    new_qty = balance.quantity_on_hand + quantity
    if new_qty < 0:
        sku = db.get(SKU, sku_id)
        sku_code = sku.sku_code if sku else str(sku_id)
        raise HTTPException(
            409,
            f"Onvoldoende voorraad voor {sku_code}: "
            f"{balance.quantity_on_hand} op voorraad, {abs(quantity)} nodig",
        )

    balance.quantity_on_hand = new_qty
    balance.last_movement_at = func.now()

    movement = StockMovement(
        sku_id=sku_id,
        organization_id=organization_id,
        movement_type=movement_type,
        quantity=quantity,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        performed_by=performed_by,
    )
    db.add(movement)
    db.flush()
    return movement


# ---------------------------------------------------------------------------
# Shipment endpoints (pakbon)
# ---------------------------------------------------------------------------

def _shipment_to_response(shipment: InboundShipment) -> ShipmentResponse:
    return ShipmentResponse(
        id=shipment.id,
        organization_id=shipment.organization_id,
        supplier_name=shipment.supplier_name,
        reference=shipment.reference,
        status=shipment.status,
        created_at=shipment.created_at,
        booked_at=shipment.booked_at,
        booked_by=shipment.booked_by,
        lines=[
            ShipmentLineResponse(
                id=line.id,
                sku_id=line.sku_id,
                sku_code=line.sku.sku_code if line.sku else "",
                sku_name=line.sku.name if line.sku else "",
                supplier_code=line.supplier_code,
                quantity=line.quantity,
            )
            for line in shipment.lines
        ],
    )


@router.post("/shipments/extract-preview", response_model=ShipmentExtractPreviewResponse)
async def extract_shipment_preview(
    file: UploadFile = File(...),
    supplier_name: str = Form(""),
    document_type: str = Form("unknown"),
    db: Session = Depends(get_db),
    user: User = Depends(require_warehouse),
):
    """Camera-first extraction preview for pakbon/factuur with bbox hints."""
    image_bytes = file.file.read()
    if not image_bytes:
        raise HTTPException(400, "Leeg bestand")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Afbeelding te groot (max 10 MB)")

    image_key = f"shipment_docs/{uuid.uuid4().hex}.jpg"
    storage.save(image_key, image_bytes)

    extracted = await extract_shipment_document(image_bytes)
    detected_type = extracted.get("document_type") or "unknown"
    if document_type in {"pakbon", "invoice"}:
        detected_type = document_type

    lines: list[ShipmentExtractedLine] = []
    normalized_supplier = supplier_name.strip()
    sku_lookup: dict[str, tuple[int, str, str]] = {}
    mapping_lookup: dict[tuple[str, str], tuple[int, str, str]] = {}
    sku_query = db.query(SKU)
    if not user.is_platform_admin:
        if user.organization_id:
            sku_query = sku_query.filter(SKU.organization_id == user.organization_id)
        else:
            sku_query = sku_query.filter(SKU.organization_id.is_(None))
    for sku in sku_query.all():
        sku_lookup[sku.sku_code.strip().upper()] = (sku.id, sku.sku_code, sku.name)

    if normalized_supplier:
        mappings = db.query(SupplierSKUMapping, SKU).join(
            SKU, SKU.id == SupplierSKUMapping.sku_id
        )
        if not user.is_platform_admin:
            mappings = mappings.filter(
                SupplierSKUMapping.organization_id == user.organization_id
            )
        for mapping, sku in mappings.filter(
            SupplierSKUMapping.supplier_name == normalized_supplier
        ).all():
            mapping_lookup[
                (mapping.supplier_name.strip().upper(), mapping.supplier_code.strip().upper())
            ] = (sku.id, sku.sku_code, sku.name)

    for row in extracted.get("lines", []):
        code = str(row.get("supplier_code", "")).strip()
        qty = int(row.get("quantity_boxes", 0) or 0)
        confidence = float(row.get("confidence", 0.0) or 0.0)
        bbox = row.get("bbox") if isinstance(row.get("bbox"), dict) else None

        matched_id = None
        matched_code = None
        matched_name = None
        if code:
            hit = mapping_lookup.get((normalized_supplier.upper(), code.upper()))
            if not hit:
                hit = sku_lookup.get(code.upper())
            if hit:
                matched_id, matched_code, matched_name = hit

        lines.append(ShipmentExtractedLine(
            supplier_code=code,
            description=str(row.get("description", "")).strip(),
            quantity_boxes=max(0, qty),
            confidence=max(0.0, min(confidence, 1.0)),
            bbox=bbox,
            matched_sku_id=matched_id,
            matched_sku_code=matched_code,
            matched_sku_name=matched_name,
        ))

    return ShipmentExtractPreviewResponse(
        supplier_name=(supplier_name.strip() or extracted.get("supplier_name", "")),
        reference=str(extracted.get("reference", "") or ""),
        document_type=detected_type,
        lines=lines,
        image_url=storage.url(image_key),
        raw_text=str(extracted.get("raw_text", "") or ""),
    )


@router.post("/shipments", response_model=ShipmentResponse, status_code=201)
def create_shipment(
    data: ShipmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Create a new inbound shipment (pakbon) with lines."""
    sku_ids = [line.sku_id for line in data.lines]
    existing_skus = db.query(SKU.id).filter(SKU.id.in_(sku_ids)).all()
    existing_ids = {row[0] for row in existing_skus}
    missing = set(sku_ids) - existing_ids
    if missing:
        raise HTTPException(400, f"SKU's niet gevonden: {missing}")

    if not user.is_platform_admin and not user.organization_id:
        raise HTTPException(400, "User has no organization")
    org_id = user.organization_id
    normalized_supplier_name = data.supplier_name.strip() if data.supplier_name else None

    shipment = InboundShipment(
        organization_id=org_id,
        supplier_name=normalized_supplier_name,
        reference=data.reference,
        status="draft",
    )
    db.add(shipment)
    db.flush()

    for line in data.lines:
        db.add(InboundShipmentLine(
            shipment_id=shipment.id,
            sku_id=line.sku_id,
            supplier_code=line.supplier_code.strip() if line.supplier_code else None,
            quantity=line.quantity,
        ))
        if normalized_supplier_name and line.supplier_code:
            normalized_code = line.supplier_code.strip()
            if normalized_code:
                existing_mapping = (
                    db.query(SupplierSKUMapping)
                    .filter(
                        SupplierSKUMapping.organization_id == org_id,
                        SupplierSKUMapping.supplier_name == normalized_supplier_name,
                        SupplierSKUMapping.supplier_code == normalized_code,
                    )
                    .first()
                )
                if existing_mapping:
                    existing_mapping.sku_id = line.sku_id
                else:
                    db.add(SupplierSKUMapping(
                        organization_id=org_id,
                        supplier_name=normalized_supplier_name,
                        supplier_code=normalized_code,
                        sku_id=line.sku_id,
                    ))
    db.commit()
    db.refresh(shipment)

    publish_event(
        "shipment_created",
        details={
            "shipment_id": shipment.id,
            "reference": shipment.reference,
            "line_count": len(data.lines),
        },
        user=user,
        resource_type="shipment",
        resource_id=shipment.id,
    )

    return _shipment_to_response(shipment)


@router.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        db.query(InboundShipment)
        .options(joinedload(InboundShipment.lines).joinedload(InboundShipmentLine.sku))
    )
    if not user.is_platform_admin:
        if user.organization_id:
            query = query.filter(InboundShipment.organization_id == user.organization_id)
        else:
            return []
    shipments = query.order_by(InboundShipment.created_at.desc()).all()
    return [_shipment_to_response(s) for s in shipments]


@router.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    shipment = (
        db.query(InboundShipment)
        .options(joinedload(InboundShipment.lines).joinedload(InboundShipmentLine.sku))
        .filter(InboundShipment.id == shipment_id)
        .first()
    )
    if not shipment:
        raise HTTPException(404, "Pakbon niet gevonden")
    return _shipment_to_response(shipment)


@router.post("/shipments/{shipment_id}/book", response_model=ShipmentResponse)
def book_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Book a shipment: create stock movements for all lines and update balances."""
    shipment = (
        db.query(InboundShipment)
        .options(joinedload(InboundShipment.lines).joinedload(InboundShipmentLine.sku))
        .filter(InboundShipment.id == shipment_id)
        .with_for_update()
        .first()
    )
    if not shipment:
        raise HTTPException(404, "Pakbon niet gevonden")
    if shipment.status != "draft":
        raise HTTPException(400, "Pakbon is al geboekt")

    for line in shipment.lines:
        apply_stock_movement(
            db,
            sku_id=line.sku_id,
            organization_id=shipment.organization_id,
            quantity=line.quantity,
            movement_type="receive",
            reference_type="shipment",
            reference_id=shipment.id,
            performed_by=user.id,
        )

    shipment.status = "booked"
    shipment.booked_at = func.now()
    shipment.booked_by = user.id
    db.commit()
    db.refresh(shipment)

    publish_event(
        "shipment_booked",
        details={
            "shipment_id": shipment.id,
            "reference": shipment.reference,
            "line_count": len(shipment.lines),
            "total_quantity": sum(l.quantity for l in shipment.lines),
        },
        user=user,
        resource_type="shipment",
        resource_id=shipment.id,
    )

    return _shipment_to_response(shipment)


# ---------------------------------------------------------------------------
# Inventory endpoints
# ---------------------------------------------------------------------------

@router.get("/inventory", response_model=list[InventoryBalanceResponse])
def list_inventory(
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        db.query(InventoryBalance)
        .join(SKU, InventoryBalance.sku_id == SKU.id)
    )
    # Scope by organization
    if user.is_platform_admin:
        if organization_id:
            query = query.filter(InventoryBalance.organization_id == organization_id)
    elif user.organization_id:
        query = query.filter(InventoryBalance.organization_id == user.organization_id)
    else:
        return []

    balances = query.order_by(SKU.name).all()
    return [
        InventoryBalanceResponse(
            sku_id=b.sku_id,
            sku_code=b.sku.sku_code,
            sku_name=b.sku.name,
            organization_id=b.organization_id,
            quantity_on_hand=b.quantity_on_hand,
            last_movement_at=b.last_movement_at,
        )
        for b in balances
    ]


def _calc_effective_price(
    unit_price: float | None,
    discount_type: str | None,
    discount_value: float | None,
    default_price: float | None,
) -> float | None:
    """Calculate effective price using the waterfall: unit_price > discount > default_price."""
    if unit_price is not None:
        return unit_price
    if default_price is not None and discount_type and discount_value is not None:
        if discount_type == "percentage":
            return round(default_price * (1 - discount_value / 100), 2)
        elif discount_type == "fixed":
            return round(max(0, default_price - discount_value), 2)
    return default_price


@router.get("/inventory/overview", response_model=list[InventoryOverviewItem])
def inventory_overview(
    search: str | None = None,
    wijntype: str | None = None,
    producent: str | None = None,
    in_stock_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Full inventory overview for merchants: stock, attributes, prices per customer."""
    if not user.is_platform_admin and not user.organization_id:
        return []
    org_id = user.organization_id

    # Start from SKU with LEFT JOIN to InventoryBalance so all products show up
    query = (
        db.query(SKU, InventoryBalance)
        .outerjoin(
            InventoryBalance,
            (InventoryBalance.sku_id == SKU.id)
            & (InventoryBalance.organization_id == org_id),
        )
        .options(
            joinedload(SKU.attributes),
            joinedload(SKU.reference_images),
        )
        .filter(SKU.active.is_(True))
    )

    if not user.is_platform_admin:
        query = query.filter(SKU.organization_id == org_id)

    if in_stock_only:
        query = query.filter(InventoryBalance.quantity_on_hand > 0)

    if search:
        query = query.filter(SKU.name.ilike(f"%{search}%"))

    if wijntype:
        query = query.filter(
            SKU.id.in_(
                db.query(SKUAttribute.sku_id).filter(
                    SKUAttribute.key == "wijntype",
                    SKUAttribute.value.ilike(f"%{wijntype}%"),
                )
            )
        )

    if producent:
        query = query.filter(
            SKU.id.in_(
                db.query(SKUAttribute.sku_id).filter(
                    SKUAttribute.key == "producent",
                    SKUAttribute.value.ilike(f"%{producent}%"),
                )
            )
        )

    rows = query.order_by(SKU.name).all()

    # Batch-load customer prices for all SKUs in result
    sku_ids = [sku.id for sku, _ in rows]
    customer_prices_rows = (
        db.query(CustomerSKU, Customer.name)
        .join(Customer, CustomerSKU.customer_id == Customer.id)
        .filter(CustomerSKU.sku_id.in_(sku_ids))
        .all()
    ) if sku_ids else []

    # Build a lookup of default_price per sku for effective price calculation
    sku_default_prices: dict[int, float | None] = {}
    for sku, _ in rows:
        sku_default_prices[sku.id] = float(sku.default_price) if sku.default_price is not None else None

    # Group by sku_id
    prices_by_sku: dict[int, list[CustomerPriceResponse]] = {}
    for cs, cname in customer_prices_rows:
        unit = float(cs.unit_price) if cs.unit_price is not None else None
        dt = cs.discount_type
        dv = float(cs.discount_value) if cs.discount_value is not None else None
        effective = _calc_effective_price(unit, dt, dv, sku_default_prices.get(cs.sku_id))
        prices_by_sku.setdefault(cs.sku_id, []).append(
            CustomerPriceResponse(
                customer_id=cs.customer_id,
                customer_name=cname,
                unit_price=unit,
                discount_type=dt,
                discount_value=dv,
                effective_price=effective,
            )
        )

    result = []
    for sku, balance in rows:
        first_image = next(
            (img for img in sku.reference_images if img.processing_status == "done"),
            None,
        )
        image_url = f"/api/files/{first_image.image_path}" if first_image else None

        result.append(
            InventoryOverviewItem(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.name,
                attributes=sku.attributes_dict,
                default_price=float(sku.default_price) if sku.default_price is not None else None,
                quantity_on_hand=balance.quantity_on_hand if balance else 0,
                last_movement_at=balance.last_movement_at if balance else None,
                image_url=image_url,
                customer_prices=prices_by_sku.get(sku.id, []),
            )
        )

    return result


@router.put("/skus/{sku_id}/price", response_model=InventoryOverviewItem)
def update_default_price(
    sku_id: int,
    data: UpdateDefaultPriceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")
    if not user.is_platform_admin and sku.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    sku.default_price = data.default_price
    db.commit()
    db.refresh(sku)

    # Return a minimal overview item
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.organization_id == (user.organization_id or sku.organization_id),
        )
        .first()
    )

    return InventoryOverviewItem(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        attributes=sku.attributes_dict,
        default_price=float(sku.default_price) if sku.default_price is not None else None,
        quantity_on_hand=balance.quantity_on_hand if balance else 0,
        last_movement_at=balance.last_movement_at if balance else None,
    )


@router.put("/customers/{customer_id}/skus/{sku_id}/price")
def update_customer_price(
    customer_id: int,
    sku_id: int,
    data: UpdateCustomerPriceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    link = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "Klant-SKU koppeling niet gevonden")

    link.unit_price = data.unit_price
    db.commit()

    return {"ok": True}


@router.put("/customers/{customer_id}/skus/{sku_id}/discount")
def update_customer_sku_discount(
    customer_id: int,
    sku_id: int,
    data: UpdateCustomerSKUDiscountRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Klant niet gevonden")
    if not user.is_platform_admin and customer.organization_id != user.organization_id:
        raise HTTPException(403, "Geen toegang")

    link = (
        db.query(CustomerSKU)
        .filter(CustomerSKU.customer_id == customer_id, CustomerSKU.sku_id == sku_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "Klant-SKU koppeling niet gevonden")

    link.discount_type = data.discount_type
    link.discount_value = data.discount_value
    db.commit()

    # Return effective price info
    sku = db.get(SKU, sku_id)
    default_price = float(sku.default_price) if sku and sku.default_price is not None else None
    unit = float(link.unit_price) if link.unit_price is not None else None
    dt = link.discount_type
    dv = float(link.discount_value) if link.discount_value is not None else None

    return {
        "ok": True,
        "discount_type": dt,
        "discount_value": dv,
        "effective_price": _calc_effective_price(unit, dt, dv, default_price),
    }


@router.get("/inventory/{sku_id}/movements", response_model=list[StockMovementResponse])
def list_movements(
    sku_id: int,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(StockMovement).filter(StockMovement.sku_id == sku_id)
    if user.is_platform_admin:
        if organization_id:
            query = query.filter(StockMovement.organization_id == organization_id)
    elif user.organization_id:
        query = query.filter(StockMovement.organization_id == user.organization_id)
    else:
        return []
    return query.order_by(StockMovement.created_at.desc()).all()


@router.post("/inventory/adjust", response_model=StockMovementResponse)
def adjust_inventory(
    data: InventoryAdjustRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Manual stock adjustment (positive or negative delta)."""
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    if not user.is_platform_admin and not user.organization_id:
        raise HTTPException(400, "User has no organization")

    movement = apply_stock_movement(
        db,
        sku_id=data.sku_id,
        organization_id=user.organization_id,
        quantity=data.quantity,
        movement_type="adjust",
        reference_type="manual",
        note=data.note,
        performed_by=user.id,
    )
    db.commit()
    db.refresh(movement)

    publish_event(
        "inventory_adjusted",
        details={
            "sku_code": sku.sku_code,
            "quantity": data.quantity,
            "note": data.note,
        },
        user=user,
        resource_type="inventory",
        resource_id=movement.id,
    )

    return movement


@router.post("/inventory/count", response_model=StockMovementResponse)
def count_inventory(
    data: InventoryCountRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_product_manager),
):
    """Physical count: set stock to absolute value by computing delta."""
    sku = db.get(SKU, data.sku_id)
    if not sku:
        raise HTTPException(404, "SKU niet gevonden")

    if not user.is_platform_admin and not user.organization_id:
        raise HTTPException(400, "User has no organization")

    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.sku_id == data.sku_id,
            InventoryBalance.organization_id == user.organization_id,
        )
        .first()
    )
    current = balance.quantity_on_hand if balance else 0
    delta = data.counted_quantity - current

    if delta == 0:
        raise HTTPException(200, "Telling komt overeen met huidige voorraad, geen wijziging nodig")

    movement = apply_stock_movement(
        db,
        sku_id=data.sku_id,
        organization_id=user.organization_id,
        quantity=delta,
        movement_type="count",
        reference_type="manual",
        note=data.note or f"Telling: {current} → {data.counted_quantity}",
        performed_by=user.id,
    )
    db.commit()
    db.refresh(movement)

    publish_event(
        "inventory_counted",
        details={
            "sku_code": sku.sku_code,
            "previous": current,
            "counted": data.counted_quantity,
            "delta": delta,
        },
        user=user,
        resource_type="inventory",
        resource_id=movement.id,
    )

    return movement
