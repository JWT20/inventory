from datetime import datetime

from typing import Literal

import unicodedata

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules import DEFAULT_MODULES


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
    organization_slug: str | None = None
    custom_label: str | None = None
    customer_id: int | None = None
    enabled_modules: list[str] = []


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str


class PushConfigResponse(BaseModel):
    enabled: bool
    public_key: str = ""


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., min_length=1)
    auth: str = Field(..., min_length=1)


class PushSubscriptionUpsert(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=4096)
    keys: PushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Push-endpoint moet HTTPS gebruiken")
        return value


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=4096)


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
    organization_slug: str | None = None
    custom_label: str | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    enabled_modules: list[str] = []
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Organization ---

def _validate_modules(modules: list[str]) -> list[str]:
    from app.modules import normalize_modules
    try:
        return normalize_modules(modules)
    except ValueError as exc:
        raise ValueError(str(exc))


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)
    custom_label: str | None = Field(None, max_length=255)
    enabled_modules: list[str] = Field(default_factory=lambda: list(DEFAULT_MODULES))
    auto_inactivate_no_images: bool = False

    @field_validator("enabled_modules")
    @classmethod
    def check_modules(cls, v: list[str]) -> list[str]:
        return _validate_modules(v)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=100)
    custom_label: str | None = None
    enabled_modules: list[str] | None = None
    auto_inactivate_no_images: bool | None = None

    @field_validator("enabled_modules")
    @classmethod
    def check_modules(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _validate_modules(v)


class OrganizationResponse(BaseModel):
    id: int
    name: str
    slug: str
    custom_label: str | None = None
    enabled_modules: list[str] = []
    auto_inactivate_no_images: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ModuleCatalogEntry(BaseModel):
    key: str
    label: str
    description: str
    baseline: bool


# --- SKU ---

WINE_ATTRIBUTE_KEYS = ("producent", "wijnaam", "wijntype", "volume")


def is_valid_ean13(code: str) -> bool:
    """True if `code` is a syntactically valid EAN-13 (13 digits, checkdigit OK).

    The 13th digit is a checksum over the first 12: odd positions weigh 1, even
    positions weigh 3, and the check digit makes the weighted sum a multiple of
    10. Catching a typo here keeps a broken barcode out of the catalogue, where
    it would later surface as a "product not found" at the scan station.
    """
    if not code.isdigit() or len(code) != 13:
        return False
    digits = [int(c) for c in code]
    checksum = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
    return (10 - checksum % 10) % 10 == digits[12]


def normalize_ean(value: str | None) -> str | None:
    """Trim an EAN to its digits, returning None for blank input."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def generate_wine_sku_code(attrs: dict[str, str]) -> str:
    """Generate SKU code from wine attributes like CHAT-GRAN-ROO-750."""
    def abbrev(s: str, length: int = 4) -> str:
        normalized = unicodedata.normalize("NFKD", s)
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        cleaned = ascii_only.strip().upper().replace(" ", "")
        return cleaned[:length]

    parts = [
        abbrev(attrs["producent"]),
        abbrev(attrs["wijnaam"]),
        abbrev(attrs["wijntype"], 3),
        attrs["volume"].strip().replace("ml", "").replace("cl", ""),
    ]
    return "-".join(part for part in parts if part)


def generate_wine_display_name(attrs: dict[str, str]) -> str:
    parts = [
        part.strip()
        for part in [attrs["producent"], attrs["wijnaam"], attrs["wijntype"]]
        if part.strip()
    ]
    if not attrs["producent"].strip() and attrs["volume"].strip():
        parts.append(f"{attrs['volume'].strip()} ml")
    return " ".join(parts)


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class SupplierResponse(BaseModel):
    id: int
    name: str
    organization_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    sku_code: str | None = None
    name: str | None = None
    category: str = "wine"
    attributes: dict[str, str] = {}
    active: bool = True
    supplier_id: int | None = None
    is_bottle: bool = False
    source_product_id: str | None = Field(default=None, max_length=100)
    # When omitted, the type is derived from the category below: wine → vision,
    # everything else → barcode (the new default, matching the model/migration).
    product_type: Literal["barcode", "vision"] | None = None
    ean: str | None = None

    @field_validator("attributes")
    @classmethod
    def validate_wine_attributes(cls, v: dict[str, str], info) -> dict[str, str]:
        category = info.data.get("category", "wine")
        if category == "wine":
            missing = [k for k in WINE_ATTRIBUTE_KEYS if k not in v or not v[k].strip()]
            if missing:
                raise ValueError(f"Wijn-attributen ontbreken: {', '.join(missing)}")
        return v

    @model_validator(mode="after")
    def resolve_type_and_validate_ean(self) -> "SKUCreate":
        # Derive the identification method from the category when not given, so
        # a caller that omits product_type gets vision for wine and barcode for
        # anything else — instead of a blanket default that contradicts the
        # model's "barcode" server default.
        if self.product_type is None:
            self.product_type = "vision" if self.category == "wine" else "barcode"
        ean = normalize_ean(self.ean)
        if self.product_type == "barcode":
            if not ean:
                raise ValueError("EAN is verplicht voor barcode-producten")
            if not is_valid_ean13(ean):
                raise ValueError(
                    "Ongeldige EAN-13 (controleer de cijfers en het controlecijfer)"
                )
        elif ean:
            raise ValueError("Een vision-product mag geen EAN hebben")
        self.ean = ean
        return self


class SKUUpdate(BaseModel):
    sku_code: str | None = None
    name: str | None = None
    attributes: dict[str, str] | None = None
    active: bool | None = None
    supplier_id: int | None = None
    is_bottle: bool | None = None
    source_product_id: str | None = Field(default=None, max_length=100)
    product_type: Literal["barcode", "vision"] | None = None
    # EAN format/uniqueness is validated in the endpoint, where the SKU's
    # product_type (existing or just-changed) and organization are known.
    ean: str | None = None


class SKUResponse(BaseModel):
    id: int
    sku_code: str
    name: str
    description: str | None
    active: bool
    category: str | None = None
    attributes: dict[str, str] = {}
    supplier_id: int | None = None
    supplier_name: str | None = None
    is_bottle: bool = False
    source_product_id: str | None = None
    product_type: str = "vision"
    ean: str | None = None
    created_at: datetime
    updated_at: datetime
    image_count: int = 0

    model_config = {"from_attributes": True}


class SKUOption(BaseModel):
    """Lightweight SKU projection for pickers (no attributes/images).

    Carries the same fields the product page searches on (category, producent,
    supplier) so picker search boxes can filter client-side on all of them.
    """
    id: int
    sku_code: str
    name: str
    is_bottle: bool = False
    category: str | None = None
    producent: str | None = None
    supplier_name: str | None = None

    model_config = {"from_attributes": True}


class AdviceProductSyncResponse(BaseModel):
    """Result of one manually triggered advice-product snapshot pull."""

    received: int
    created: int
    updated: int
    deactivated: int
    conflicts: list[str] = []

    model_config = {"from_attributes": True}


# --- Reference Image ---
class ReferenceImageResponse(BaseModel):
    id: int
    sku_id: int
    image_path: str
    vision_description: str | None = None
    processing_status: str = "done"
    processing_error_code: str | None = None
    processing_error_message: str | None = None
    duplicate_sku_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReferenceImageStatusResponse(BaseModel):
    """Lightweight projection used for polling — no image_path or description."""

    id: int
    processing_status: str
    processing_error_code: str | None = None

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
VALID_DELIVERY_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
DEFAULT_DELIVERY_DAYS = ("wednesday", "thursday", "friday")


def _normalize_delivery_days(days: list[str] | None) -> list[str]:
    if days is None:
        return list(DEFAULT_DELIVERY_DAYS)
    normalized: list[str] = []
    for day in days:
        if day not in VALID_DELIVERY_DAYS:
            raise ValueError(f"Ongeldige leverdag: {day}")
        if day not in normalized:
            normalized.append(day)
    if not normalized:
        raise ValueError("Selecteer minimaal één leverdag")
    return normalized


class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    organization_id: int | None = None
    show_prices: bool = True
    discount_percentage: float | None = Field(None, ge=0, le=100)
    delivery_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday"] = "thursday"
    delivery_days: list[Literal["monday", "tuesday", "wednesday", "thursday", "friday"]] = Field(
        default_factory=lambda: list(DEFAULT_DELIVERY_DAYS)
    )

    @field_validator("delivery_days")
    @classmethod
    def validate_delivery_days(cls, v: list[str]) -> list[str]:
        return _normalize_delivery_days(v)


class CustomerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    show_prices: bool | None = None
    discount_percentage: float | None = None
    delivery_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday"] | None = None
    delivery_days: list[Literal["monday", "tuesday", "wednesday", "thursday", "friday"]] | None = None

    @field_validator("delivery_days")
    @classmethod
    def validate_delivery_days(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _normalize_delivery_days(v)


class CustomerResponse(BaseModel):
    id: int
    name: str
    show_prices: bool = True
    discount_percentage: float | None = None
    delivery_day: str = "thursday"
    delivery_days: list[str] = []
    sku_ids: list[int] = []
    sku_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerSKUResponse(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    default_price: float | None = None
    unit_price: float | None = None
    discount_type: str | None = None
    discount_value: float | None = None
    effective_price: float | None = None

    model_config = {"from_attributes": True}


class CustomerSKUAdd(BaseModel):
    sku_ids: list[int] = Field(..., min_length=1)


class CustomerSKUReorder(BaseModel):
    sku_ids: list[int] = Field(..., min_length=1)


# --- Order ---
class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    klant: str
    customer_id: int | None = None
    customer_name: str = ""
    delivery_day: str = "thursday"
    quantity: int
    booked_count: int
    has_image: bool
    is_bottle: bool = False
    is_item: bool = False
    # Scannable code of the product's primary pick location (barcode products
    # only). NULL for vision/wine and for barcode products without a location.
    pick_location: str | None = None
    show_prices: bool = True
    unit_price: float | None = None
    discount_type: str | None = None
    discount_value: float | None = None
    effective_price: float | None = None
    line_total: float | None = None

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    reference: str
    status: str
    # Order provenance: "manual" (in-app/customer), "shopify" or "bol".
    channel: str = "manual"
    # How this order is picked: "vision" (camera + AI) or "barcode" (handscanner
    # EAN scan). Derived from the order's products so the courier UI can route to
    # the right scanner.
    pick_method: str = "vision"
    remarks: str = ""
    delivery_week: str | None = None
    allowed_delivery_days: list[str] = []
    organization_id: int | None = None
    organization_name: str = ""
    created_by_name: str = ""
    created_at: datetime
    ordered_at: datetime | None = None
    updated_at: datetime
    customer_name: str | None = None
    lines: list[OrderLineResponse] = []
    # Box and bottle lines are separate order units; the totals never mix.
    total_boxes: int = 0
    booked_boxes: int = 0
    total_bottles: int = 0
    booked_bottles: int = 0
    total_items: int = 0
    booked_items: int = 0
    visible_total: float | None = None
    hidden_lines_count: int = 0

    model_config = {"from_attributes": True}


class OrderLineDeleteResponse(BaseModel):
    """Result of deleting an order line.

    When the deleted line was the last one, the whole order is removed and
    ``order_deleted`` is true with ``order`` left empty. Otherwise ``order``
    holds the updated order.
    """

    order_deleted: bool = False
    order: OrderResponse | None = None


class WeeklyPickPhotoResponse(BaseModel):
    order_line_id: int
    order_line_ids: list[int]
    sku_id: int
    wine_name: str
    image_url: str | None = None
    quantity: int
    booked_count: int
    customers: list[str] = []


class NextPickResponse(BaseModel):
    sku_id: int
    sku_name: str
    order_line_id: int
    image_url: str | None = None
    remaining_quantity: int
    source: Literal["this_order", "other_order"]
    order_id: int
    customer_name: str | None = None


class ChannelSyncSummary(BaseModel):
    """Result of a channel pull/sync run."""
    fetched: int
    created: int
    updated: int
    unmatched: int


class InventoryPushSummary(BaseModel):
    """Result of a one-shot bulk push of local stock to Shopify."""
    total: int
    pushed: int
    skipped_no_variant: int
    failed: int


class ChannelModeRequest(BaseModel):
    """Set a channel connection to ``observe`` or ``live`` (the go-live cutover)."""
    mode: str


class ChannelConnectUrl(BaseModel):
    url: str


class ChannelStatus(BaseModel):
    connected: bool
    shop_domain: str | None = None
    mode: str | None = None
    last_synced_at: datetime | None = None


class ChannelReviewResolveRequest(BaseModel):
    """Resolve a cancelled channel order after picking already started."""
    action: Literal["cancel_restock", "cancel_without_restock"]


class ChannelReviewResolveResponse(BaseModel):
    status: str


class ChannelOrderRow(BaseModel):
    # Internal order id — lets the reconciliation UI resolve a needs_review order.
    order_id: int | None = None
    external_id: str
    reference: str | None = None
    # The channel's human order number (Shopify order name, e.g. "1262"); this is
    # what the courier's shipping label carries as its reference.
    channel_reference: str | None = None
    # The fulfillment state at the source channel ("fulfilled"/"unfulfilled"/…);
    # fulfilled orders will be kept out of the pick list at cutover.
    channel_fulfillment_status: str | None = None
    review_reason: str | None = None
    ordered_at: datetime | None = None
    channel_shipped_at: datetime | None = None
    status: str | None = None
    matched_lines: int
    unmatched_eans: list[str] = []


class ChannelReconciliation(BaseModel):
    status: ChannelStatus
    orders: list[ChannelOrderRow]
    unmatched_eans: list[str]


class EanScanRequest(BaseModel):
    order_id: int = Field(..., gt=0)
    ean: str = Field(..., min_length=1)
    # The location the courier last scanned. When the product has pick locations
    # this must match one of them (hufterproef: no picking from the wrong shelf).
    # Optional so orders without located products keep working unchanged.
    location_code: str | None = None


class EanScanResponse(BaseModel):
    """Result of one EAN scan = one booked unit on a barcode order."""
    order_id: int
    order_line_id: int
    sku_id: int
    sku_code: str
    sku_name: str
    klant: str
    rolcontainer: str
    booked_quantity: int
    remaining_quantity: int
    order_completed: bool
    # Id of the booking this scan created, so the courier can undo exactly this
    # unit if they grabbed the wrong/damaged item.
    booking_id: int


class UndoScanRequest(BaseModel):
    booking_id: int = Field(..., gt=0)


class UndoScanResponse(BaseModel):
    """Result of undoing one previously scanned unit on a barcode order."""
    order_id: int
    order_line_id: int
    sku_id: int
    remaining_quantity: int
    order_status: str


class LabelScanRequest(BaseModel):
    order_id: int = Field(..., gt=0)
    label_reference: str = Field(..., min_length=1)


class LabelOrderOpenRequest(BaseModel):
    label_reference: str = Field(..., min_length=1)


class LabelOrderOpenResponse(BaseModel):
    """Order resolved from the barcode on a loose Veloyd shipping label."""
    order_id: int
    tracking_code: str


class LabelScanResponse(BaseModel):
    """Result of the shipping-label verification gate on a barcode order."""
    order_id: int
    status: str
    reference: str


class ManualOrderLineCreate(BaseModel):
    customer_id: int = Field(..., gt=0)
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    delivery_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday"] | None = None


class ManualOrderCreate(BaseModel):
    organization_id: int | None = None
    remarks: str = ""
    lines: list[ManualOrderLineCreate] = Field(..., min_length=1)


class OrderLineAdd(BaseModel):
    customer_id: int = Field(..., gt=0)
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    delivery_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday"] | None = None


class OrderUpdate(BaseModel):
    remarks: str


class OrderApprove(BaseModel):
    # ISO week to deliver in, e.g. "2026-W24". Defaults to the week of approval.
    week: str | None = None
    # Optional merchant override, limited to the customer's configured days.
    delivery_day: Literal["monday", "tuesday", "wednesday", "thursday", "friday"] | None = None
    # When True, lines whose SKU still lacks a reference image are moved to a
    # new sibling order (pending_images) so the original order can go active
    # immediately. Lines that already have bookings stay put.
    split_unimaged: bool = False


class OrderLineUpdate(BaseModel):
    quantity: int = Field(..., gt=0)


class BookingResponse(BaseModel):
    id: int
    order_id: int
    order_line_id: int = 0
    order_reference: str
    context_order_id: int | None = None
    context_order_reference: str | None = None
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
    order_completed: bool = False

    model_config = {"from_attributes": True}


class BookingConfirmation(BaseModel):
    """Returned when a scan requires human approval before booking."""
    needs_confirmation: bool = True
    confirmation_token: str
    order_id: int = 0
    order_line_id: int = 0
    order_reference: str = ""
    context_order_id: int | None = None
    context_order_reference: str | None = None
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
    cap_for_customer: int | None = None
    ordered_by_customer: int | None = None


class ConfirmBookingRequest(BaseModel):
    confirmation_token: str
    quantity: int = Field(1, ge=1)


class MissingReferenceCandidate(BaseModel):
    """A SKU on the order that has no usable reference image yet."""
    sku_id: int
    sku_code: str
    sku_name: str
    remaining_quantity: int


class RegisterReferenceRequest(BaseModel):
    register_token: str
    sku_id: int = Field(..., gt=0)


class SKUDistributionLine(BaseModel):
    """One customer's demand for a scanned SKU. Display only — books nothing."""
    order_id: int
    order_line_id: int
    customer_name: str
    rolcontainer: str
    delivery_day: str
    delivery_week: str | None = None
    ordered_quantity: int
    booked_count: int
    # Remaining bookable now, respecting the fair weekly allocation cap.
    remaining_quantity: int
    # Fully delivered (booked >= ordered) — shown with a ✓, not hidden.
    is_complete: bool
    # Belongs to the order the koerier started scanning in.
    is_context_order: bool


class SKUDistributionResponse(BaseModel):
    """Read-only verdeel-lijst: which customers a scanned SKU still needs to go to."""
    sku_id: int
    sku_code: str
    sku_name: str
    scope: str
    total_remaining: int
    lines: list[SKUDistributionLine]


# --- Inbound Shipments ---

class ShipmentLineCreate(BaseModel):
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    supplier_code: str | None = None


class ShipmentCreate(BaseModel):
    supplier_name: str | None = None
    reference: str | None = None
    document_sha256: str | None = None
    upload_attempt_id: int | None = Field(None, gt=0)
    force: bool = False
    lines: list[ShipmentLineCreate] = Field(..., min_length=1)


class ShipmentTextExtractRequest(BaseModel):
    text: str = Field(..., min_length=1)
    supplier_name: str = ""
    document_type: Literal["pakbon", "invoice", "unknown"] = "unknown"


class InboundBookedSKUResponse(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    quantity: int
    is_bottle: bool = False


class ShipmentLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    supplier_code: str | None = None
    quantity: int
    is_bottle: bool = False

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
    booked_skus: list[InboundBookedSKUResponse] = []

    model_config = {"from_attributes": True}


class ShipmentExtractedLine(BaseModel):
    supplier_code: str = ""
    description: str = ""
    # quantity_boxes is in besteleenheden van de gematchte SKU: dozen voor een
    # doos-SKU, flessen voor een fles-SKU (is_bottle=True).
    quantity_boxes: int = Field(0, ge=0)
    quantity: int = Field(0, ge=0)
    quantity_unit: Literal["boxes", "pieces", "unknown"] = "unknown"
    confidence: float = Field(0.0, ge=0, le=1)
    matched_sku_id: int | None = None
    matched_sku_code: str | None = None
    matched_sku_name: str | None = None
    is_bottle: bool = False
    needs_confirmation: bool = False
    match_source: Literal["supplier_mapping", "unresolved"] = "unresolved"
    candidate_matches: list["ShipmentMatchCandidate"] = []


class ShipmentMatchCandidate(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    is_bottle: bool = False
    confidence: float = Field(0.0, ge=0, le=1)


class ShipmentExtractPreviewResponse(BaseModel):
    supplier_name: str = ""
    reference: str = ""
    document_type: str = ""
    lines: list[ShipmentExtractedLine] = []
    image_url: str = ""
    raw_text: str = ""
    upload_attempt_id: int | None = None
    document_sha256: str | None = None
    duplicate_of_shipment_id: int | None = None
    duplicate_of_status: str | None = None


class InboundUploadAttemptResponse(BaseModel):
    id: int
    source_type: str
    original_filename: str | None = None
    supplier_name: str | None = None
    reference: str | None = None
    status: str
    error_stage: str | None = None
    error_message: str | None = None
    shipment_id: int | None = None
    line_count: int = 0
    bookable_line_count: int = 0
    booked_line_count: int = 0
    booked_quantity: int = 0
    booked_skus: list[InboundBookedSKUResponse] = []
    created_at: datetime
    updated_at: datetime

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


class SupplierMappingResponse(BaseModel):
    id: int | None = None
    organization_id: int | None = None
    supplier_name: str
    supplier_code: str
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConfirmLineMatchRequest(BaseModel):
    supplier_name: str = Field(..., min_length=1)
    supplier_code: str = Field(..., min_length=1)
    chosen_sku_id: int = Field(..., gt=0)
    persist_mapping: bool = True


class InventoryBalanceResponse(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    organization_id: int | None = None
    quantity_on_hand: int
    quantity_reserved: int = 0
    quantity_available: int = 0
    last_movement_at: datetime | None

    model_config = {"from_attributes": True}


class AdviceStockItem(BaseModel):
    source_product_id: str | None = Field(default=None, min_length=1, max_length=100)
    sku_code: str = Field(..., min_length=1)
    is_bottle: bool = True
    quantity_available: int = Field(..., ge=0)


class AdviceStockResponse(BaseModel):
    items: list[AdviceStockItem]


class InventoryOverviewItem(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    active: bool = True
    attributes: dict[str, str] = {}
    # EAN-13 barcode; NULL for vision (wine) products.
    ean: str | None = None
    default_price: float | None = None
    quantity_on_hand: int = 0
    quantity_reserved: int = 0
    quantity_available: int = 0
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
    performed_by: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class InventoryAdjustRequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    quantity: int
    note: str | None = None
    organization_id: int | None = None


class InventoryCountRequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    counted_quantity: int = Field(..., ge=0)
    note: str | None = None
    organization_id: int | None = None


class UpdateDefaultPriceRequest(BaseModel):
    default_price: float | None = None


class UpdateCustomerPriceRequest(BaseModel):
    unit_price: float | None = None


# --- Product Attribute Definitions (Kenmerken) ---

class ProductAttributeValueCreate(BaseModel):
    value: str = Field(..., min_length=1, max_length=255)
    sort_order: int = 0


class ProductAttributeValueUpdate(BaseModel):
    value: str | None = Field(None, min_length=1, max_length=255)
    sort_order: int | None = None


class ProductAttributeValueResponse(BaseModel):
    id: int
    attribute_id: int
    value: str
    sort_order: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductAttributeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    values: list[ProductAttributeValueCreate] = []


class ProductAttributeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class ProductAttributeResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    description: str | None = None
    values: list[ProductAttributeValueResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


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


# --- Weekly Order Summary ---

class WeeklySummaryCustomerOrder(BaseModel):
    customer_name: str
    quantity: int
    effective_price: float | None = None
    line_total: float | None = None
    remarks: str = ""


class WeeklySummaryWine(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    default_price: float | None = None
    is_bottle: bool = False
    total_quantity: int
    current_stock: int = 0
    completed_order_count: int = 0
    closed_order_count: int = 0
    orders: list[WeeklySummaryCustomerOrder] = []
    wine_total: float | None = None


class WeeklySummarySupplier(BaseModel):
    supplier_id: int | None = None
    supplier_name: str
    wines: list[WeeklySummaryWine] = []
    # *_quantity blijft de som van alle eenheden; dozen/flessen apart ernaast.
    supplier_total_quantity: int = 0
    supplier_total_boxes: int = 0
    supplier_total_bottles: int = 0
    supplier_total_value: float | None = None


class WeeklySummaryCustomerLine(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    is_bottle: bool = False
    quantity: int
    effective_price: float | None = None
    line_total: float | None = None
    remarks: str = ""


class WeeklySummaryCustomer(BaseModel):
    customer_id: int | None = None
    customer_name: str
    lines: list[WeeklySummaryCustomerLine] = []
    customer_total_quantity: int = 0
    customer_total_boxes: int = 0
    customer_total_bottles: int = 0
    customer_total_value: float | None = None


class WeeklySummaryResponse(BaseModel):
    week: str
    group_by: Literal["supplier", "customer"] = "supplier"
    suppliers: list[WeeklySummarySupplier] = []
    customers: list[WeeklySummaryCustomer] = []
    grand_total_quantity: int = 0
    grand_total_boxes: int = 0
    grand_total_bottles: int = 0
    grand_total_value: float | None = None


class MonthlyBoxesMonth(BaseModel):
    month: str  # "YYYY-MM"
    boxes: int
    bottles: int = 0
    items: int = 0
    # Orders/lines are counted for barcode products only — hence the item_ prefix.
    item_order_count: int = 0
    item_line_count: int = 0


class MonthlyBoxesOrganization(BaseModel):
    organization_id: int | None
    organization_name: str
    total_boxes: int
    total_bottles: int = 0
    total_items: int = 0
    total_item_orders: int = 0
    total_item_lines: int = 0
    months: list[MonthlyBoxesMonth] = []


class MonthlyBoxesResponse(BaseModel):
    organizations: list[MonthlyBoxesOrganization] = []


# --- Pick locations (barcode-only, courier-managed) ------------------------

class LocationSKU(BaseModel):
    """A barcode product linked to a location."""
    sku_id: int
    sku_code: str
    name: str
    ean: str | None = None
    organization_name: str | None = None
    is_primary: bool = True


class LocationResponse(BaseModel):
    id: int
    code: str
    rij: str | None = None
    kast: str | None = None
    plank: str | None = None
    active: bool = True
    created_at: datetime
    skus: list[LocationSKU] = []

    model_config = {"from_attributes": True}


class LocationCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    rij: str | None = Field(None, max_length=20)
    kast: str | None = Field(None, max_length=20)
    plank: str | None = Field(None, max_length=20)


class LocationUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=50)
    rij: str | None = Field(None, max_length=20)
    kast: str | None = Field(None, max_length=20)
    plank: str | None = Field(None, max_length=20)
    active: bool | None = None


class LinkSKURequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    is_primary: bool = True


class AvailableSKU(BaseModel):
    """A barcode product that can be linked to a location."""
    id: int
    sku_code: str
    name: str
    ean: str | None = None
    organization_name: str | None = None


class LocationScanRequest(BaseModel):
    order_id: int = Field(..., gt=0)
    location_code: str = Field(..., min_length=1)


class LocationScanSKU(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    ean: str | None = None
    remaining_quantity: int


class LocationScanResponse(BaseModel):
    """The products of an order that live at a just-scanned location."""
    order_id: int
    location_code: str
    skus: list[LocationScanSKU] = []
