from datetime import datetime

from typing import Literal

import unicodedata

from pydantic import BaseModel, Field


# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)
    role: Literal["admin", "merchant", "courier"] = "courier"


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
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Vision / Identification ---
class MatchResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float


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


# --- Order ---
class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    klant: str
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
    klant: str = Field(..., min_length=1)
    producent: str = Field(..., min_length=1)
    wijnaam: str = Field(..., min_length=1)
    wijntype: str = Field(..., min_length=1)
    jaargang: str = Field(..., min_length=1)
    volume: str = Field(..., min_length=1)
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

    model_config = {"from_attributes": True}
