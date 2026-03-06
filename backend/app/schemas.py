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
    sku_code: str
    name: str
    description: str | None = None
    active: bool = True


class SKUUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class SKUResponse(BaseModel):
    id: int
    sku_code: str
    name: str
    description: str | None
    active: bool
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
class OrderLineCreate(BaseModel):
    sku_code: str
    quantity: int = Field(..., gt=0)


class OrderCreate(BaseModel):
    order_number: str
    customer_name: str
    dock_location: str | None = None
    lines: list[OrderLineCreate] = Field(..., min_length=1)


class OrderUpdate(BaseModel):
    customer_name: str | None = None
    dock_location: str | None = None


class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: int
    received_quantity: int
    status: str

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    order_number: str
    customer_name: str
    dock_location: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineResponse]

    model_config = {"from_attributes": True}


# --- Cross-Docking ---
class DockAssignment(BaseModel):
    order_id: int
    order_number: str
    customer_name: str
    dock_location: str | None
    line_id: int
    quantity_needed: int
    quantity_after: int


class ReceiveResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
    assignment: DockAssignment | None = None
