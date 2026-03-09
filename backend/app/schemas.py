from datetime import datetime

from typing import Literal

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
class SKUCreate(BaseModel):
    producer: str
    wine_name: str
    wine_type: str
    vintage: int | None = None
    volume: str = "0.75L"
    description: str | None = None
    active: bool = True


class SKUUpdate(BaseModel):
    producer: str | None = None
    wine_name: str | None = None
    wine_type: str | None = None
    vintage: int | None = None
    volume: str | None = None
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class SKUResponse(BaseModel):
    id: int
    sku_code: str
    name: str
    description: str | None
    active: bool
    producer: str | None = None
    wine_name: str | None = None
    wine_type: str | None = None
    vintage: int | None = None
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


# --- Orders ---
class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: int
    scanned_quantity: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    order_number: str
    customer_name: str
    status: str
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineResponse] = []

    model_config = {"from_attributes": True}


class OrderLineCreate(BaseModel):
    sku_id: int
    quantity: int = Field(..., ge=1)


class OrderCreate(BaseModel):
    order_number: str = ""
    customer_name: str
    lines: list[OrderLineCreate] = Field(..., min_length=1)


class OrderImportResult(BaseModel):
    order: OrderResponse
    new_skus: list[SKUResponse]
    existing_skus: list[SKUResponse]


class ScanResult(BaseModel):
    matched: bool
    sku_code: str | None = None
    sku_name: str | None = None
    confidence: float = 0.0
    order_line_id: int | None = None
    scanned_quantity: int = 0
    total_quantity: int = 0
    customer_name: str = ""
    message: str = ""
