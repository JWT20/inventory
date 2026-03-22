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
    role: Literal["admin", "merchant", "courier"] = "courier"

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
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- SKU ---

def generate_sku_code(producent: str, wijnaam: str, wijntype: str, jaargang: str, volume: str) -> str:
    """Generate SKU code like CHAT-GRAN-ROO-2019-750."""
    def abbrev(s: str, length: int = 4) -> str:
        normalized = unicodedata.normalize("NFKD", s)
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        cleaned = ascii_only.strip().upper().replace(" ", "")
        return cleaned[:length]

    return "-".join([
        abbrev(producent),
        abbrev(wijnaam),
        abbrev(wijntype, 3),
        jaargang.strip(),
        volume.strip().replace("ml", "").replace("cl", ""),
    ])


def generate_display_name(producent: str, wijnaam: str, wijntype: str, jaargang: str) -> str:
    return f"{producent} {wijnaam} {wijntype} {jaargang}"


class SKUCreate(BaseModel):
    producent: str = Field(..., min_length=1)
    wijnaam: str = Field(..., min_length=1)
    wijntype: str = Field(..., min_length=1)
    jaargang: str = Field(..., min_length=1)
    volume: str = Field(..., min_length=1)
    active: bool = True


class SKUUpdate(BaseModel):
    producent: str | None = None
    wijnaam: str | None = None
    wijntype: str | None = None
    jaargang: str | None = None
    volume: str | None = None
    active: bool | None = None


class SKUResponse(BaseModel):
    id: int
    sku_code: str
    name: str
    description: str | None
    active: bool
    producent: str | None = None
    wijnaam: str | None = None
    wijntype: str | None = None
    jaargang: str | None = None
    volume: str | None = None
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
    confirmation_token: str = ""


class MatchResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
    needs_confirmation: bool = False
    confirmation_reason: str | None = None
    alternatives: list[AlternativeMatch] = []


# --- CSV Row ---
class CSVRow(BaseModel):
    klant: str
    producent: str
    wijnaam: str
    type: str
    jaargang: str
    volume: str
    aantal: int

    @property
    def sku_code(self) -> str:
        return generate_sku_code(self.producent, self.wijnaam, self.type, self.jaargang, self.volume)

    @property
    def display_name(self) -> str:
        return generate_display_name(self.producent, self.wijnaam, self.type, self.jaargang)


class CSVValidationResult(BaseModel):
    matched_skus: list[SKUResponse]
    new_skus: list[SKUResponse]
    errors: list[str]


# --- Customer ---
class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)


class CustomerResponse(BaseModel):
    id: int
    name: str
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
    merchant_name: str
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
    merchant_id: int
    lines: list[ManualOrderLineCreate] = Field(..., min_length=1)


class BookingResponse(BaseModel):
    id: int
    order_id: int
    order_reference: str
    sku_code: str
    sku_name: str
    klant: str
    rolcontainer: str
    created_at: datetime
    needs_confirmation: bool = False

    model_config = {"from_attributes": True}


class BookingConfirmation(BaseModel):
    """Returned when a low-quality description requires human approval."""
    needs_confirmation: bool = True
    confirmation_token: str
    sku_code: str
    sku_name: str
    confidence: float
    scan_image_url: str
    reference_image_url: str
    alternatives: list[AlternativeMatch] = []


class ConfirmBookingRequest(BaseModel):
    confirmation_token: str
