"""Server-to-server integration endpoints."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import InventoryBalance, Organization, SKU
from app.schemas import AdviceStockItem, AdviceStockResponse

router = APIRouter(prefix="/integrations/advice", tags=["integrations"])


def _authenticate_advice_stock_request(
    x_inventory_key: str | None = Header(default=None, alias="X-Inventory-Key"),
) -> int:
    expected_key = settings.advice_stock_api_key
    organization_id = settings.advice_stock_organization_id
    if not expected_key or organization_id is None:
        raise HTTPException(503, "Advice stock integration is not configured")

    provided_key = (x_inventory_key or "").encode("utf-8")
    if not secrets.compare_digest(provided_key, expected_key.encode("utf-8")):
        raise HTTPException(401, "Invalid inventory key")

    return organization_id


@router.get("/stock", response_model=AdviceStockResponse)
def advice_stock(
    response: Response,
    db: Session = Depends(get_db),
    organization_id: int = Depends(_authenticate_advice_stock_request),
) -> AdviceStockResponse:
    """Return available bottle stock for the configured organization."""
    if db.get(Organization, organization_id) is None:
        raise HTTPException(
            503,
            "Advice stock organization is not configured correctly",
        )

    rows = (
        db.query(
            SKU.sku_code,
            InventoryBalance.quantity_on_hand,
            InventoryBalance.quantity_reserved,
        )
        .outerjoin(
            InventoryBalance,
            and_(
                InventoryBalance.sku_id == SKU.id,
                InventoryBalance.organization_id == organization_id,
            ),
        )
        .filter(
            SKU.organization_id == organization_id,
            SKU.is_bottle.is_(True),
            SKU.active.is_(True),
        )
        .order_by(SKU.sku_code)
        .all()
    )

    response.headers["Cache-Control"] = "no-store"
    return AdviceStockResponse(
        items=[
            AdviceStockItem(
                sku_code=sku_code,
                quantity_available=max(
                    (quantity_on_hand or 0) - (quantity_reserved or 0),
                    0,
                ),
            )
            for sku_code, quantity_on_hand, quantity_reserved in rows
        ]
    )
