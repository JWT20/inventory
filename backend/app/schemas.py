from datetime import datetime

from pydantic import BaseModel


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


# --- Order ---
class OrderLineCreate(BaseModel):
    sku_code: str
    quantity: int


class OrderCreate(BaseModel):
    order_number: str
    customer_name: str
    lines: list[OrderLineCreate]


class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: int
    picked_quantity: int
    status: str

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


# --- Pick ---
class PickResult(BaseModel):
    correct: bool
    confidence: float
    matched_sku_code: str | None = None
    matched_sku_name: str | None = None
    expected_sku_code: str
    expected_sku_name: str
    message: str


# --- Vision ---
class MatchResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
