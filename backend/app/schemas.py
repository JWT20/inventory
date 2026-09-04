from datetime import datetime

from typing import Literal

import unicodedata

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules import DEFAULT_MODULES

# The physical stock pools, as an API type. Kept in step with
# ``models.VALID_INVENTORY_LOCATIONS``; a Literal cannot be built from that
# tuple, so the two are written out separately on purpose.
InventoryLocation = Literal["warehouse", "store", "webshop"]


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
    advice_products_sync_available: bool = False


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
    advice_products_sync_available: bool = False
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
    # The bottle product inside this box. Only meaningful on a box product.
    bottle_sku_id: int | None = Field(default=None, gt=0)
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
    # Send null to unlink the bottle from this box.
    bottle_sku_id: int | None = Field(default=None, gt=0)
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
    bottle_sku_id: int | None = None
    bottle_sku_code: str | None = None
    bottle_sku_name: str | None = None
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
    # The bottle inside this box, when linked. A picker that offers boxes for
    # replenishment needs it to tell a usable box from one that would be
    # refused on submit.
    bottle_sku_id: int | None = None
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
    # False for a lookalike that is not open in the scan scope: it cannot be
    # booked, but the picker still needs to see its photo — it is usually the
    # box they are actually holding.
    bookable: bool = True
    # Short human-readable reason shown next to the candidate ("staat niet open
    # in deze week", or what visually separates it from the proposal).
    note: str = ""


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
    inventory_location: InventoryLocation = "warehouse"
    # "customer" (leaves the building) or "replenishment" (the merchant's own
    # stock). A replenishment order names the pool its goods land in.
    order_kind: Literal["customer", "replenishment"] = "customer"
    destination_location: InventoryLocation | None = None
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
    # Waar dit product in het magazijn ligt, als het aan een schap gekoppeld is.
    # Puur informatie: wijn wordt op foto herkend, niet op een scan van het schap.
    pick_location: str | None = None


class NextPickResponse(BaseModel):
    sku_id: int
    sku_name: str
    order_line_id: int
    image_url: str | None = None
    # Waar het product ligt, als het aan een schap gekoppeld is. Dit is het
    # scherm waar de koerier op afloopt, dus hier hoort het te staan.
    pick_location: str | None = None
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


class CarrierStatus(BaseModel):
    """Whether an organization can talk to its own carrier account."""

    carrier: str
    connected: bool
    base_url: str | None = None
    updated_at: datetime | None = None


class VeloydWebhookUrl(BaseModel):
    """The URL to paste into Veloyd, shown once because the secret is in it."""

    url: str


class VeloydWebhookAck(BaseModel):
    """What the webhook did with one event; Veloyd only reads the status code."""

    result: str


class VeloydConnectRequest(BaseModel):
    """The API key of one merchant's client account at the carrier."""

    api_key: str = Field(..., min_length=8, max_length=200)
    # Only for a carrier that runs a second Veloyd install; NULL keeps the
    # configured default.
    base_url: str | None = Field(default=None, max_length=255)


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


class ReplenishmentOrderLineCreate(BaseModel):
    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


class ReplenishmentOrderCreate(BaseModel):
    """An order the merchant places for their own shop or webshop stock.

    No customer: the goods stay with the merchant. The quantity is in the
    product's own unit — boxes for a box product, bottles for a bottle product —
    exactly as picking counts them.
    """

    organization_id: int | None = None
    # Only the two sellable pools; the warehouse is where the goods come from.
    destination_location: Literal["store", "webshop"]
    remarks: str = ""
    lines: list[ReplenishmentOrderLineCreate] = Field(..., min_length=1)


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
    # Waar dit product ligt, als het aan een schap gekoppeld is — handig om de
    # volgende van dezelfde wijn te halen. Alleen tonen, nooit scannen: wijn
    # wordt op foto herkend.
    pick_location: str | None = None
    scan_image_url: str
    reference_image_url: str
    reference_image_urls: list[str] = []
    alternatives: list[AlternativeMatch] = []
    remaining_quantity: int = 0
    cap_for_customer: int | None = None
    ordered_by_customer: int | None = None
    # Why this scan needs a human look (low confidence, lookalikes, rerank
    # unavailable). Empty when the match was clean.
    confirmation_reason: str | None = None
    # True only for the narrow rejected-all fallback: the visual pass could not
    # confirm any photo, but one plausible, near-tied SKU is open on the order.
    # The UI must present it as an explicit manual decision, never as a clean hit.
    manual_review_required: bool = False


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
    # Everything arrives at the warehouse. What goes to the shop or the webshop
    # is decided afterwards, per product, by moving or replenishing it — not by
    # a choice on the delivery note, which could only ever apply to the whole
    # document at once. The field stays so an older caller keeps working; the
    # response side stays wide because shipments booked to the shop before this
    # still have to be readable.
    inventory_location: Literal["warehouse"] = "warehouse"
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
    inventory_location: Literal["warehouse", "store"] = "warehouse"
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
    # Which pool the goods landed in, taken from the booked shipment. NULL while
    # an attempt has no shipment yet — nothing was booked anywhere.
    inventory_location: InventoryLocation | None = None
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
    inventory_location: InventoryLocation = "warehouse"
    quantity_on_hand: int
    quantity_reserved: int = 0
    quantity_available: int = 0
    last_movement_at: datetime | None

    model_config = {"from_attributes": True}


# What the advice app may ask the stock feed for. "sellable" is the shop and
# the webshop together — one pool spread over two physical places, which is what
# the webshop can actually sell.
AdviceStockPool = Literal["warehouse", "store", "webshop", "sellable"]


class AdviceStockItem(BaseModel):
    source_product_id: str | None = Field(default=None, min_length=1, max_length=100)
    sku_code: str = Field(..., min_length=1)
    is_bottle: bool = True
    # What the requested pool has. For "sellable" this is shop + webshop.
    quantity_available: int = Field(..., ge=0)
    # The split behind that number, so the shop can say where a bottle is. Both
    # are filled for every pool request; for a single-pool request the other one
    # is simply what that other shelf happens to hold.
    quantity_store: int = Field(default=0, ge=0)
    quantity_webshop: int = Field(default=0, ge=0)


class AdviceStockResponse(BaseModel):
    items: list[AdviceStockItem]


class AdviceSaleLineIn(BaseModel):
    source_product_id: str = Field(..., min_length=1, max_length=100)
    # Negative books a return back onto the shelf. Zero is meaningless and
    # rejected so a client bug cannot masquerade as a successful report.
    quantity: int

    @field_validator("quantity")
    @classmethod
    def _non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("quantity mag niet 0 zijn")
        return v


class AdviceSaleRequest(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=100)
    channel: Literal["pos"] = "pos"
    occurred_at: datetime | None = None
    lines: list[AdviceSaleLineIn] = Field(..., min_length=1)


class AdviceSaleAppliedLine(BaseModel):
    source_product_id: str
    sku_code: str
    quantity: int
    quantity_available: int


class AdviceSaleResponse(BaseModel):
    sale_id: str
    # Lines booked by *this* call. A retry reports its lines under `duplicate`
    # instead, so the caller can tell a first delivery from a repeat.
    applied: list[AdviceSaleAppliedLine]
    duplicate: list[str]
    # Product ids with no linked bottle SKU. The sale is still accepted for the
    # rest; run the advice product sync and re-post to book these too.
    unmatched: list[str]


class AdviceReservationLineIn(BaseModel):
    source_product_id: str = Field(..., min_length=1, max_length=100)
    quantity: int = Field(..., gt=0)


class AdviceReservationRequest(BaseModel):
    external_order_id: str = Field(..., min_length=1, max_length=100)
    order_reference: str | None = Field(default=None, max_length=100)
    # A counter pickup is handed over from the shop, a delivery is packed from
    # the webshop shelf. The caller decides per order and the choice is stored,
    # so a reservation is always settled against the pool it was taken from.
    fulfillment_method: Literal["pickup", "dockscan"] = "pickup"
    # The pool the route starts from. Either may spill over into the other —
    # they are one sellable pool — so this is a preference, not a limit, and
    # what is actually held is recorded per line.
    #
    # A delivery used to be routed from the warehouse. That name is still
    # accepted and now means the webshop shelf, so the advice app and Dockscan
    # can be deployed one at a time instead of in the same breath. Drop it once
    # no caller sends it any more.
    inventory_location: Literal["warehouse", "store", "webshop"] = "store"
    lines: list[AdviceReservationLineIn] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_route(self) -> "AdviceReservationRequest":
        if self.inventory_location == "warehouse":
            self.inventory_location = "webshop"
        # Crossing the two would start the search on the far shelf and quietly
        # empty the one the other route depends on first.
        expected = "store" if self.fulfillment_method == "pickup" else "webshop"
        if self.inventory_location != expected:
            raise ValueError(
                f"{self.fulfillment_method} hoort bij voorraadlocatie {expected}"
            )
        return self


class AdviceReservationLineResponse(BaseModel):
    source_product_id: str
    sku_code: str
    quantity: int


class AdviceReservationResponse(BaseModel):
    external_order_id: str
    order_reference: str | None = None
    fulfillment_method: Literal["pickup", "dockscan"] = "pickup"
    inventory_location: InventoryLocation = "store"
    status: Literal["active", "collected", "released"]
    duplicate: bool = False
    lines: list[AdviceReservationLineResponse]


class AdviceReservationAdminLine(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    source_product_id: str | None = None
    quantity: int
    # Which shelf these bottles are held on. A hold spread over the shop and the
    # webshop shows as two rows, because that is where they physically are.
    inventory_location: InventoryLocation = "store"


class AdviceReservationAdminItem(BaseModel):
    """One advice-app hold, as the merchant sees it in Dockscan."""

    id: int
    external_order_id: str
    # The human order number from the advice app ("JUR-2026-000123"). This is
    # what the merchant can actually look up on the other side.
    order_reference: str | None = None
    fulfillment_method: Literal["pickup", "dockscan"] = "pickup"
    inventory_location: InventoryLocation = "store"
    status: Literal["active", "collected", "released"]
    created_at: datetime
    collected_at: datetime | None = None
    released_at: datetime | None = None
    total_quantity: int
    lines: list[AdviceReservationAdminLine]


class DeliveryAddressIn(BaseModel):
    """Where a delivery order goes, as the advice app hands it over.

    Every field is a snapshot taken when the order was placed. House number and
    suffix stay apart because carriers ask for them separately.
    """

    recipient_name: str = Field(..., min_length=1, max_length=200)
    street: str = Field(..., min_length=1, max_length=200)
    house_number: str = Field(..., min_length=1, max_length=20)
    house_number_suffix: str | None = Field(default=None, max_length=20)
    postal_code: str = Field(..., min_length=1, max_length=20)
    city: str = Field(..., min_length=1, max_length=120)
    # ISO 3166-1 alpha-2. Defaulted rather than required: today every delivery is
    # domestic, and a caller that omits it means the Netherlands.
    country: str = Field(default="NL", min_length=2, max_length=2)
    phone: str | None = Field(default=None, max_length=40)
    # The carrier mails the track-and-trace here. Optional: the advice app only
    # started sending it with this release.
    email: str | None = Field(default=None, max_length=255)

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()


class DeliveryAddressResponse(BaseModel):
    recipient_name: str
    street: str
    house_number: str
    house_number_suffix: str | None = None
    postal_code: str
    city: str
    country: str
    phone: str | None = None
    email: str | None = None

    model_config = {"from_attributes": True}


class AdviceOrderLineIn(BaseModel):
    source_product_id: str = Field(..., min_length=1, max_length=100)
    quantity: int = Field(..., gt=0)


class AdviceOrderRequest(BaseModel):
    """One paid delivery order from the advice app.

    Deliveries only. A pickup order stays in the advice app and reserves store
    stock through ``/reservations``; giving it an order here would put it in the
    pick list, which is exactly what that design avoids.
    """

    external_order_id: str = Field(..., min_length=1, max_length=100)
    # The number the customer sees ("JUR-2026-8CERZC"). Optional for the same
    # reason as on a reservation, but this is what a merchant looks the order up
    # by, and later what a shipping label carries as its reference.
    order_reference: str | None = Field(default=None, max_length=100)
    fulfillment_method: Literal["dockscan"] = "dockscan"
    # A delivery is packed from the webshop shelf. "warehouse" is still accepted
    # so the advice app keeps working across the two deploys, and is folded into
    # "webshop" below — otherwise the same caller would land in two different
    # pools depending on whether it happened to send the field at all.
    inventory_location: Literal["warehouse", "webshop"] = "webshop"
    # When the customer placed the order, not when Dockscan heard about it. The
    # reconciliation view sorts on this.
    ordered_at: datetime | None = None
    delivery_address: DeliveryAddressIn
    lines: list[AdviceOrderLineIn] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _fold_legacy_pool(self) -> "AdviceOrderRequest":
        if self.inventory_location == "warehouse":
            self.inventory_location = "webshop"
        return self


class AdviceOrderMatchedLine(BaseModel):
    source_product_id: str
    sku_code: str
    quantity: int


class AdviceOrderResponse(BaseModel):
    external_order_id: str
    order_id: int
    # Dockscan's own reference ("ADV-1A2B3C4D"), distinct from the advice app's.
    reference: str
    status: str
    duplicate: bool = False
    matched: list[AdviceOrderMatchedLine]
    # Products the catalogue does not know. The order still lands, so the
    # operator can link the product and re-post rather than lose the order.
    unmatched: list[str]


class AdviceOrderAdminLine(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: int


class AdviceOrderParcel(BaseModel):
    """One box of a delivery order at the carrier."""

    sequence: int
    veloyd_parcel_id: str
    # NULL until the carrier prints the label; Veloyd assigns it there.
    tracking_code: str | None = None
    tracking_url: str | None = None
    # Set when Veloyd reported the print, which is when this box stopped being
    # cancellable. NULL means it can still be pulled back.
    label_printed_at: datetime | None = None

    model_config = {"from_attributes": True}


class AdviceOrderAdminItem(BaseModel):
    """One advice-app delivery order, as the merchant sees it in Kanalen."""

    order_id: int
    # Dockscan's own reference; the advice app's number is `order_reference`.
    reference: str
    external_order_id: str | None = None
    order_reference: str | None = None
    status: str
    ordered_at: datetime | None = None
    created_at: datetime
    total_quantity: int
    delivery_address: DeliveryAddressResponse | None = None
    lines: list[AdviceOrderAdminLine]
    # Products the advice app sent that the catalogue does not know. They have no
    # order line, so without this the order would look complete but ship short.
    unmatched_products: list[str] = []
    # The boxes registered at the carrier. Empty while the order is not ready to
    # ship, or when Veloyd could not be reached yet.
    parcels: list[AdviceOrderParcel] = []


class InventoryOverviewItem(BaseModel):
    sku_id: int
    sku_code: str = ""
    sku_name: str = ""
    active: bool = True
    attributes: dict[str, str] = {}
    # EAN-13 barcode; NULL for vision (wine) products.
    ean: str | None = None
    default_price: float | None = None
    inventory_location: InventoryLocation = "warehouse"
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
    inventory_location: InventoryLocation = "warehouse"
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
    inventory_location: InventoryLocation = "warehouse"


class InventoryTransferRequest(BaseModel):
    sku_id: int
    quantity: int = Field(..., gt=0)
    from_location: InventoryLocation
    to_location: InventoryLocation
    note: str | None = None
    organization_id: int | None = None

    @model_validator(mode="after")
    def _check_locations(self) -> "InventoryTransferRequest":
        if self.from_location == self.to_location:
            raise ValueError("Bron en bestemming moeten verschillen")
        return self


class InventoryTransferBalance(BaseModel):
    inventory_location: InventoryLocation
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int


class InventoryTransferResponse(BaseModel):
    sku_id: int
    quantity: int
    from_location: InventoryLocation
    to_location: InventoryLocation
    # Both sides after the move, so the caller never has to guess what the
    # other pool now holds.
    balances: list[InventoryTransferBalance]


class InventoryCountRequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    counted_quantity: int = Field(..., ge=0)
    note: str | None = None
    organization_id: int | None = None
    inventory_location: InventoryLocation = "warehouse"


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


class SellableStockItem(BaseModel):
    """One bottle product with the stock the webshop can actually sell.

    Shop and webshop are separate physical places but one sellable pool, so both
    are reported next to their total — you need the split to know where to walk,
    and the total to know whether to reorder.
    """

    sku_id: int
    sku_code: str
    sku_name: str
    store: int = 0
    webshop: int = 0
    total: int = 0
    # Wat er in het magazijn ligt om mee bij te vullen. Dozen en flessen apart:
    # ze staan er ook apart, en een doos moet nog gepickt worden voordat er
    # flessen op de plank staan. Niet meegeteld in `total` — dit is niet te
    # verkopen, het is wat je kunt bijbestellen.
    warehouse_boxes: int = 0
    warehouse_bottles: int = 0


class WeeklySummaryResponse(BaseModel):
    week: str
    group_by: Literal["supplier", "customer"] = "supplier"
    suppliers: list[WeeklySummarySupplier] = []
    customers: list[WeeklySummaryCustomer] = []
    # Independent of the week: what is on the shop and webshop shelves right
    # now, so a shortage can be spotted and replenished from the same screen.
    sellable_stock: list[SellableStockItem] = []
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
    # Al het werk dat geen webshoppakket is: weekorders voor klanten, orders uit
    # een verkoopkanaal, en bevoorrading van de eigen plank.
    organizations: list[MonthlyBoxesOrganization] = []
    # Bezorgorders uit de wijnadvies-app. Apart omdat een pakketje met een label
    # ander werk is dan een pallet kisten voor een restaurant, en ook anders
    # afgerekend wordt.
    webshop: list[MonthlyBoxesOrganization] = []
    # Of deze handelaar überhaupt webshoporders kán hebben. Zonder
    # wijnadvies-koppeling is dat nooit zo, en dan hoort het tabje er niet te
    # staan. Mét koppeling maar zonder gepickte order hoort het er wél te staan,
    # leeg — anders verschijnt het pas als er halverwege een maand iets in valt.
    webshop_connected: bool = False


# --- Pick locations (barcode-only, courier-managed) ------------------------

class LocationSKU(BaseModel):
    """A product linked to a location."""
    sku_id: int
    sku_code: str
    name: str
    ean: str | None = None
    organization_name: str | None = None
    is_primary: bool = True
    # Loose bottles are picked by photo but still live on a shelf, so the pick
    # screen can show where to walk even though there is no barcode to scan.
    is_bottle: bool = False


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


class LocationBulkCreate(BaseModel):
    """A rectangle of shelves: every row x cabinet x shelf number in the ranges.

    Filling a warehouse one location at a time is hundreds of identical forms,
    which is both slow and the reason codes end up inconsistent. The code is
    built from a template so the label on the shelf and the code in the system
    are decided in the same breath.
    """

    rijen: list[str] = Field(..., min_length=1, max_length=26)
    kasten: list[str] = Field(..., min_length=1, max_length=26)
    plank_van: int = Field(..., ge=0, le=999)
    plank_tot: int = Field(..., ge=0, le=999)
    # Placeholders {rij}, {kast} and {plank}. The shelf number is padded to
    # ``plank_cijfers`` so codes sort the way a human walks the aisle: without
    # it "10" lands between "1" and "2".
    code_template: str = Field(default="{rij}-{kast}-{plank}", max_length=50)
    plank_cijfers: int = Field(default=2, ge=1, le=3)
    # Preview only — nothing is written. The rectangle is easy to get wrong by
    # an order of magnitude, and undoing a thousand rows by hand is not a fix.
    dry_run: bool = True

    @model_validator(mode="after")
    def _check_range(self) -> "LocationBulkCreate":
        if self.plank_tot < self.plank_van:
            raise ValueError("Plank tot moet groter of gelijk zijn aan plank van")
        placeholders = {"{rij}", "{kast}", "{plank}"}
        if not any(token in self.code_template for token in placeholders):
            raise ValueError(
                "Code-sjabloon moet minstens {rij}, {kast} of {plank} bevatten"
            )
        return self


class LocationBulkPreviewItem(BaseModel):
    code: str
    rij: str
    kast: str
    plank: str
    # True when a location with this code already exists; it is skipped rather
    # than failing the whole batch, so a partly-filled aisle can be topped up.
    bestaat_al: bool = False


class LocationBulkResponse(BaseModel):
    dry_run: bool
    totaal: int = 0
    aangemaakt: int = 0
    overgeslagen: int = 0
    # Capped for the preview; ``totaal`` always counts them all.
    voorbeeld: list[LocationBulkPreviewItem] = []


class LinkSKURequest(BaseModel):
    sku_id: int = Field(..., gt=0)
    is_primary: bool = True


class AvailableSKU(BaseModel):
    """A product that can be linked to a location."""
    id: int
    sku_code: str
    name: str
    ean: str | None = None
    organization_name: str | None = None
    is_bottle: bool = False


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
