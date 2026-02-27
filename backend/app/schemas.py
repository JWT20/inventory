from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="picker", pattern="^(admin|picker|viewer)$")


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    role: str | None = Field(default=None, pattern="^(admin|picker|viewer)$")
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    refresh_token: str


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
