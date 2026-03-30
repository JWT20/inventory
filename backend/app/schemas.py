from datetime import datetime

from typing import Literal

import unicodedata

from pydantic import BaseModel, Field, field_validator


# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    username: str
    role: str
    is_platform_admin: bool = False
    organization_id: int | None = None
    organization_name: str | None = None
    customer_id: int | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str


def _validate_password(password: str) -> str:
    from app.auth import validate_password_strength
    errors = validate_password_strength(password)
    if errors:
        raise ValueError("; ".join(errors))
    return password


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["owner", "member", "courier", "customer"] = "courier"
    organization_id: int | None = None
    customer_id: int | None = None

    @field_validator("password")
    @classmethod
    def check_password_strength(cls, v: str) -> str:
        return _validate_password(v)


class AdminResetPassword(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def check_password_strength(cls, v: str) -> str:
        return _validate_password(v)


class ChangeOwnPassword(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def check_password_strength(cls, v: str) -> str:
        return _validate_password(v)


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_platform_admin: bool = False
    organization_id: int | None = None
    organization_name: str | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Organization ---

class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)
    enabled_modules: list[str] = ["inventory", "orders"]


class OrganizationResponse(BaseModel):
    id: int
    name: str
    slug: str
    enabled_modules: list[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# --- SKU ---

WINE_ATTRIBUTE_KEYS = ("producent", "wijnaam", "wijntype", "jaargang", "volume")


def generate_wine_sku_code(attrs: dict[str, str]) -> str:
    """Generate SKU code from wine attributes like CHAT-GRAN-ROO-2019-750."""
    def abbrev(s: str, length: int = 4) -> str:
        normalized = unicodedata.normalize("NFKD", s)
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        cleaned = ascii_only.strip().upper().replace(" ", "")
        return cleaned[:length]

    return "-".join([
        abbrev(attrs["producent"]),
        abbrev(attrs["wijnaam"]),
        abbrev(attrs["wijntype"], 3),
        attrs["jaargang"].strip(),
        attrs["volume"].strip().replace("ml", "").replace("cl", ""),
    ])


def generate_wine_display_name(attrs: dict[str, str]) -> str:
    return f"{attrs['producent']} {attrs['wijnaam']} {attrs['wijntype']} {attrs['jaargang']}"


class SKUCreate(BaseModel):
    sku_code: str | None = None
    name: str | None = None
    category: str = "wine"
    attributes: dict[str, str] = {}
    active: bool = True

    @field_validator("attributes")
    @classmethod
    def validate_wine_attributes(cls, v: dict[str, str], info) -> dict[str, str]:
        category = info.data.get("category", "wine")
        if category == "wine":
            missing = [k for k in WINE_ATTRIBUTE_KEYS if k not in v or not v[k].strip()]
            if missing:
                raise ValueError(f"Wijn-attributen ontbreken: {', '.join(missing)}")
        return v


class SKUUpdate(BaseModel):
    attributes: dict[str, str] | None = None
    active: bool | None = None


class SKUResponse(BaseModel):
    id: int
    sku_code: str
    name: str
    description: str | None
    active: bool
    category: str | None = None
    attributes: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime
    image_count: int = 0

    model_config = {"from_attributes": True}


# --- Reference Image ---
class ReferenceImageResponse(BaseModel):
    id: int
    sku_id: int
    image_path: str
    vision_description: str | None = None
    processing_status: str = "done"
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Vision / Identification ---
class AlternativeMatch(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
    reference_image_url: str = ""
    reference_image_urls: list[str] = []
    confirmation_token: str = ""


class MatchResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
    needs_confirmation: bool = False
    confirmation_reason: str | None = None
    alternatives: list[AlternativeMatch] = []
    scan_image_url: str = ""
    reference_image_urls: list[str] = []


# --- Customer ---
class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    organization_id: int | None = None
    show_prices: bool = True


class CustomerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    show_prices: bool | None = None


class CustomerResponse(BaseModel):
    id: int
    name: str
    show_prices: bool = True
    sku_ids: list[int] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Order ---
class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    klant: str
    customer_id: int | None = None
    customer_name: str = ""
    quantity: int
    booked_count: int
    has_image: bool

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    reference: str
    status: str
    organization_name: str = ""
    created_by_name: str = ""
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineResponse] = []
    total_boxes: int = 0
    booked_boxes: int = 0

    model_config = {"from_attributes": True}


class ManualOrderLineCreate(BaseModel):
    customer_id: int = Field(..., gt=0)
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


class ManualOrderCreate(BaseModel):
    organization_id: int | None = None
    lines: list[ManualOrderLineCreate] = Field(..., min_length=1)


class BookingResponse(BaseModel):
    id: int
    order_id: int
    order_reference: str
    sku_id: int = 0
    sku_code: str
    sku_name: str
    klant: str
    rolcontainer: str
    created_at: datetime
    needs_confirmation: bool = False
    scan_image_url: str = ""
    reference_image_urls: list[str] = []
    confidence: float = 0.0
    booked_quantity: int = 1
    remaining_quantity: int = 0

    model_config = {"from_attributes": True}


class BookingConfirmation(BaseModel):
    """Returned when a scan requires human approval before booking."""
    needs_confirmation: bool = True
    confirmation_token: str
    sku_code: str
    sku_name: str
    confidence: float
    klant: str = ""
    rolcontainer: str = ""
    scan_image_url: str
    reference_image_url: str
    reference_image_urls: list[str] = []
    alternatives: list[AlternativeMatch] = []
    remaining_quantity: int = 0


class ConfirmBookingRequest(BaseModel):
    confirmation_token: str
    quantity: int = Field(1, ge=1)


# --- Inbound Shipments ---

class ShipmentLineCreate(BaseModel):
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


class ShipmentCreate(BaseModel):
    supplier_name: str | None = None
    reference: str | None = None
    lines: list[ShipmentLineCreate] = Field(..., min_length=1)


class ShipmentLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    quantity: int

    model_config = {"from_attributes": True}


class ShipmentResponse(BaseModel):
    id: int
    organization_id: int | None = None
    supplier_name: str | None
    reference: str | None
    status: str
    created_at: datetime
    booked_at: datetime | None
    booked_by: int | None
    lines: list[ShipmentLineResponse] = []

    model_config = {"from_attributes": True}


# --- Inventory ---

class CustomerPriceResponse(BaseModel):
    customer_id: int
    customer_name: str
    unit_price: float | None = None
    discount_type: str | None = None
    discount_value: float | None = None
    effective_price: float | None = None

    model_config = {"from_attributes": True}


class InventoryBalanceResponse(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    organization_id: int | None = None
    quantity_on_hand: int
    last_movement_at: datetime | None

    model_config = {"from_attributes": True}


class InventoryOverviewItem(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    attributes: dict[str, str] = {}
    default_price: float | None = None
    quantity_on_hand: int = 0
    last_movement_at: datetime | None = None
    image_url: str | None = None
    customer_prices: list[CustomerPriceResponse] = []

    model_config = {"from_attributes": True}


class StockMovementResponse(BaseModel):
    id: int
    sku_id: int
    organization_id: int | None = None
    movement_type: str
    quantity: int
    reference_type: str | None
    reference_id: int | None
    note: str | None
    performed_by: int
    created_at: datetime

    model_config = {"from_attributes": True}


class InventoryAdjustRequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    quantity: int
    note: str | None = None


class InventoryCountRequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    counted_quantity: int = Field(..., ge=0)
    note: str | None = None


class UpdateDefaultPriceRequest(BaseModel):
    default_price: float | None = None


class UpdateCustomerPriceRequest(BaseModel):
    unit_price: float | None = None


class UpdateCustomerSKUDiscountRequest(BaseModel):
    discount_type: str | None = None
    discount_value: float | None = None

    @field_validator("discount_type")
    @classmethod
    def validate_discount_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("percentage", "fixed"):
            raise ValueError("discount_type moet 'percentage' of 'fixed' zijn")
        return v

    @field_validator("discount_value")
    @classmethod
    def validate_discount_value(cls, v: float | None, info) -> float | None:
        dtype = info.data.get("discount_type")
        if dtype is not None and v is None:
            raise ValueError("discount_value is verplicht als discount_type is ingesteld")
        if v is not None and dtype is None:
            raise ValueError("discount_type is verplicht als discount_value is ingesteld")
        if v is not None and v < 0:
            raise ValueError("discount_value moet positief zijn")
        if dtype == "percentage" and v is not None and v > 100:
            raise ValueError("Percentage korting mag niet hoger dan 100 zijn")
        return v
