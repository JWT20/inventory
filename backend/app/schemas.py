from datetime import datetime

from pydantic import BaseModel, Field


# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)
    is_admin: bool = False


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
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
