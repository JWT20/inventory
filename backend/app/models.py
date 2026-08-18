import datetime
import json

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# gemini-embedding-001 default full-fidelity output
EMBEDDING_DIM = 3072


VALID_ROLES = ("owner", "member", "courier", "customer")
# How a product is identified. "barcode" = matched on its EAN/GTIN scan (no
# vision); "vision" = matched via reference photos + AI (the wine flow).
VALID_PRODUCT_TYPES = ("barcode", "vision")
VALID_SHIPMENT_STATUSES = ("draft", "booked")
VALID_INBOUND_UPLOAD_STATUSES = ("processing", "needs_action", "draft", "booked", "failed")
VALID_MOVEMENT_TYPES = ("receive", "pick", "adjust", "count", "sale", "transfer")
VALID_INVENTORY_LOCATIONS = ("warehouse", "store")
VALID_DISCOUNT_TYPES = ("percentage", "fixed")
VALID_DELIVERY_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
DEFAULT_DELIVERY_DAYS = ("wednesday", "thursday", "friday")
DEFAULT_DELIVERY_DAYS_JSON = json.dumps(list(DEFAULT_DELIVERY_DAYS))


class Organization(Base):
    """A merchant organization (e.g. 'Wijnhandel De Druif')."""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    custom_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # New organizations default to the barcode-fulfilment baseline; vision is
    # the exception, enabled per-merchant. Kept in sync with app.modules.
    enabled_modules: Mapped[str] = mapped_column(
        Text, default='["inventory","orders","barcode_picking","channel_orders"]'
    )
    auto_inactivate_no_images: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="organization")

    @property
    def modules(self) -> list[str]:
        return json.loads(self.enabled_modules)

    @modules.setter
    def modules(self, value: list[str]) -> None:
        self.enabled_modules = json.dumps(value)


class User(Base):
    """User model compatible with FastAPI-Users.

    Roles:
    - owner: merchant organization owner (has organization_id)
    - member: merchant organization member (has organization_id)
    - courier: platform-level courier (no organization)
    - customer: customer who can place orders (has organization_id)

    Platform admin is a separate flag (is_platform_admin), not a role.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(1024))
    role: Mapped[str] = mapped_column(String(20), default="courier")
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization | None"] = relationship(back_populates="users")
    customer: Mapped["Customer | None"] = relationship()
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.is_platform_admin

    @property
    def can_manage_products(self) -> bool:
        return self.is_platform_admin or self.role in ("owner", "member")


class PushSubscription(Base):
    """One browser/device that opted in to Web Push for a user."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Browser push endpoints are opaque capability URLs and can be fairly long.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="push_subscriptions")
    deliveries: Mapped[list["PushDelivery"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


class PushDelivery(Base):
    """Transactional outbox row for one event sent to one subscription."""

    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "event_key", "subscription_id", name="uq_push_delivery_event_subscription"
        ),
        Index(
            "ix_push_deliveries_pending",
            "sent_at",
            "failed_at",
            "next_attempt_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="/")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    next_attempt_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    subscription: Mapped["PushSubscription"] = relationship(
        back_populates="deliveries"
    )


class Supplier(Base):
    """A supplier (leverancier) that wines can be ordered from."""
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_supplier_org_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()


class SKU(Base):
    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint(
            "source_product_id IS NULL OR is_bottle = true",
            name="ck_skus_advice_product_is_bottle",
        ),
        Index("ix_skus_org_active_name", "organization_id", "active", "name"),
        # An EAN identifies one product within a merchant. Uniqueness is scoped
        # per organization (not global like sku_code) because the courier serves
        # many merchants and two of them may legitimately stock the same EAN; a
        # pick-time scan always resolves within the order's organization. NULL
        # eans (every wine/vision product) are excluded so they never collide.
        Index(
            "uq_skus_org_ean",
            "organization_id",
            "ean",
            unique=True,
            postgresql_where=text("ean IS NOT NULL"),
            sqlite_where=text("ean IS NOT NULL"),
        ),
        # The advice app owns this stable product-family id. Only bottle SKUs
        # use it; uniqueness per merchant makes repeated product-feed imports
        # idempotent without coupling unrelated organizations.
        Index(
            "uq_skus_org_source_product_id",
            "organization_id",
            "source_product_id",
            unique=True,
            postgresql_where=text("source_product_id IS NOT NULL"),
            sqlite_where=text("source_product_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    default_price: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    # True = ordered/scanned/booked per single bottle; False = per box.
    is_bottle: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    source_product_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    # What the advice app last said about commercial availability. NULL for
    # unlinked SKUs. Kept separate from `active` because a linked bottle is only
    # sellable when the feed wants it *and* it has a usable reference image.
    source_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # How the product is identified. New products default to "barcode"; the
    # existing wine catalogue is backfilled to "vision" by the migration.
    product_type: Mapped[str] = mapped_column(
        String(20), default="barcode", server_default=text("'barcode'"), nullable=False
    )
    # EAN-13 barcode. NULL for vision (wine) products. Unique per organization
    # via uq_skus_org_ean above.
    ean: Mapped[str | None] = mapped_column(String(13), nullable=True)
    # Cached Shopify inventory_item_id for this product's variant, resolved once
    # via the variant barcode (== ean) and reused for inventory write-back. NULL
    # until first resolved, or when the org has no live Shopify connection.
    shopify_inventory_item_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Cached bol Offer API id for this EAN. SKU is organization-scoped, so the
    # value is automatically bound to the same merchant as the bol connection.
    # Cleared whenever the EAN or bol credentials change, and re-resolved after
    # a stale-id 404.
    bol_offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    reference_images: Mapped[list["ReferenceImage"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )
    attributes: Mapped[list["SKUAttribute"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )
    organization: Mapped["Organization | None"] = relationship()
    supplier: Mapped["Supplier | None"] = relationship()
    # Pick locations this barcode product lives at. Empty for vision/wine
    # products, which are never shelf-picked (enforced in the locations router).
    location_links: Mapped[list["SKULocation"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )

    @property
    def attributes_dict(self) -> dict[str, str]:
        """Return attributes as a {key: value} dictionary."""
        return {a.key: a.value for a in self.attributes}

    def set_attribute(self, key: str, value: str) -> None:
        """Set a single attribute, updating if exists or creating if new."""
        for attr in self.attributes:
            if attr.key == key:
                attr.value = value
                return
        self.attributes.append(SKUAttribute(key=key, value=value))

    def set_attributes(self, attrs: dict[str, str]) -> None:
        """Bulk-set attributes from a dictionary."""
        for key, value in attrs.items():
            self.set_attribute(key, value)


class SKUAttribute(Base):
    __tablename__ = "sku_attributes"
    __table_args__ = (
        UniqueConstraint("sku_id", "key"),
        Index("ix_sku_attributes_key_value_sku_id", "key", "value", "sku_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(500))

    sku: Mapped["SKU"] = relationship(back_populates="attributes")


class ReferenceImage(Base):
    __tablename__ = "reference_images"
    __table_args__ = (
        Index(
            "ix_reference_images_sku_status_created",
            "sku_id",
            "processing_status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    image_path: Mapped[str] = mapped_column(String(500))
    vision_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(20), default="done"
    )  # "pending", "processing", "done", "failed"
    processing_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    processing_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    duplicate_sku_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description_quality: Mapped[str | None] = mapped_column(String(10), nullable=True)
    wine_check_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sku: Mapped["SKU"] = relationship(back_populates="reference_images")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    show_prices: Mapped[bool] = mapped_column(Boolean, default=True)
    discount_percentage: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    delivery_day: Mapped[str] = mapped_column(
        String(20), default="thursday", server_default="thursday"
    )
    delivery_days_raw: Mapped[str] = mapped_column(
        "delivery_days",
        Text,
        default=DEFAULT_DELIVERY_DAYS_JSON,
        server_default=DEFAULT_DELIVERY_DAYS_JSON,
        nullable=False,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()
    sku_links: Mapped[list["CustomerSKU"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

    @property
    def delivery_days(self) -> list[str]:
        try:
            raw = json.loads(self.delivery_days_raw or "[]")
        except (TypeError, json.JSONDecodeError):
            raw = []
        days: list[str] = []
        for day in (raw if isinstance(raw, list) else []):
            if day in VALID_DELIVERY_DAYS and day not in days:
                days.append(day)
        if days:
            return days
        if self.delivery_day in VALID_DELIVERY_DAYS:
            return [self.delivery_day]
        return list(DEFAULT_DELIVERY_DAYS)

    @delivery_days.setter
    def delivery_days(self, value: list[str]) -> None:
        days: list[str] = []
        for day in value:
            if day in VALID_DELIVERY_DAYS and day not in days:
                days.append(day)
        self.delivery_days_raw = json.dumps(days or list(DEFAULT_DELIVERY_DAYS))


class CustomerSKU(Base):
    __tablename__ = "customer_skus"
    __table_args__ = (
        UniqueConstraint("customer_id", "sku_id"),
        Index("ix_customer_skus_sku_id", "sku_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    unit_price: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    discount_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discount_value: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    customer: Mapped["Customer"] = relationship(back_populates="sku_links")
    sku: Mapped["SKU"] = relationship()


VALID_ORDER_STATUSES = ("pending_approval", "pending_images", "pending_product", "needs_review", "active", "completed", "cancelled", "closed", "observed")
# "observed": a channel order imported in observe-mode — visible for
# reconciliation but inert (not pickable, no stock movement). Promoted to
# "active" at cutover (live mode).


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # Speeds up the monthly booked-boxes report which filters finalized
        # orders by terminal status.
        Index("ix_orders_status_finalized_at", "status", "finalized_at"),
        # A channel order (Shopify/bol) carries the source channel's order id so
        # the same external order is never imported twice. Uniqueness is scoped
        # per (organization, channel); manual orders have a NULL external_id and
        # are excluded so they never collide.
        Index(
            "uq_orders_org_channel_external",
            "organization_id",
            "channel",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_orders_veloyd_tracking_code",
            "veloyd_tracking_code",
            unique=True,
            postgresql_where=text("veloyd_tracking_code IS NOT NULL"),
            sqlite_where=text("veloyd_tracking_code IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reference: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    # Where the order came from: "manual" (created in-app or by a customer),
    # "shopify" or "bol". Provenance + dedup only — it does NOT drive the order
    # lifecycle; whether an order is born active is decided by the organization's
    # modules (see create_order).
    channel: Mapped[str] = mapped_column(
        String(20), default="manual", server_default=text("'manual'"), nullable=False
    )
    # Snapshot of the physical stock pool this order fulfils from. Existing and
    # normal Dockscan orders are warehouse work; future source integrations may
    # route a newly-created order explicitly without moving older orders.
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="warehouse", server_default=text("'warehouse'"), nullable=False
    )
    # The order id at the source channel (Shopify/bol). NULL for manual orders.
    # Unique per (organization, channel) via uq_orders_org_channel_external.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The human order number at the source channel (Shopify order name, e.g.
    # "1262"), normalized without the leading '#'. Distinct from external_id,
    # which is the channel's internal order id. This is the number the courier's
    # shipping label (Veloyd) carries as its reference, so the later label-scan
    # pick step matches on this. NULL for manual orders.
    channel_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Normalized unique track-and-trace barcode printed on the physical Veloyd
    # label. It is learned when the courier first scans the loose label to open the
    # order, then reused for lookup and checked again at the final shipping gate.
    veloyd_tracking_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The fulfillment state at the source channel (Shopify displayFulfillmentStatus,
    # e.g. "fulfilled" / "unfulfilled"). Refreshed on every sync. Surfaced in
    # observe so already-shipped orders are visible; the cutover keeps fulfilled
    # orders out of the pick list. NULL for manual orders.
    channel_fulfillment_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Why a channel order is parked in needs_review. Persisted so resolve actions
    # can be gated on the actual operational cause instead of guessing from the
    # latest reconciliation snapshot.
    review_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The order's timestamp at the source channel (e.g. Shopify createdAt). NULL
    # for manual orders. Distinct from created_at, which is the row insert time —
    # the reconciliation view sorts/shows by this, not the import moment.
    ordered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # Latest shipment timestamp reported by the source channel. Used by the
    # read-only bol history view; NULL means the order has not been shipped.
    channel_shipped_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remarks: Mapped[str] = mapped_column(Text, default="", server_default="")
    delivery_week: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # When the order first reached a terminal state (completed/closed). Used for
    # the monthly booked-boxes report so a box counts in the month the order was
    # finalized, independent of later edits to updated_at.
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    organization: Mapped["Organization | None"] = relationship()
    creator: Mapped["User | None"] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    # Only a delivery order has one. See OrderDeliveryAddress for why it is not
    # a set of columns here.
    delivery_address: Mapped["OrderDeliveryAddress | None"] = relationship(
        back_populates="order", cascade="all, delete-orphan", uselist=False
    )

    def mark_finalized(self) -> None:
        """Stamp ``finalized_at`` the first time the order reaches a terminal
        state (completed or closed). Idempotent: later calls are no-ops."""
        if self.finalized_at is None:
            self.finalized_at = datetime.datetime.utcnow()


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    klant: Mapped[str] = mapped_column(String(150), default="")
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    booked_count: Mapped[int] = mapped_column(Integer, default=0)
    # Stable source-line identity + fulfillment counters. These let a channel
    # fulfillment be matched against stock already deducted by an in-app pick;
    # only the unmatched remainder is an external/home stock movement.
    channel_line_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_current_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_unfulfilled_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_fulfilled_seen: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    channel_fulfilled_from_app: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    channel_fulfilled_external: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    delivery_day: Mapped[str] = mapped_column(
        String(20), default="thursday", server_default="thursday"
    )

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()
    customer: Mapped["Customer | None"] = relationship()

    @property
    def customer_name(self) -> str:
        if self.customer:
            return self.customer.name
        return self.klant


class Booking(Base):
    """1 scan = 1 besteleenheid (doos of fles) = 1 booking."""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    scanned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scan_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    order: Mapped["Order"] = relationship(back_populates="bookings")
    order_line: Mapped["OrderLine"] = relationship()
    sku: Mapped["SKU"] = relationship()


class InboundShipment(Base):
    """Pakbon / delivery note for incoming goods."""
    __tablename__ = "inbound_shipments"
    __table_args__ = (
        Index(
            "ux_inbound_shipments_org_supplier_ref",
            "organization_id",
            "supplier_name",
            "reference",
            unique=True,
            postgresql_where=text(
                "reference IS NOT NULL AND reference <> '' AND status <> 'cancelled'"
            ),
            sqlite_where=text(
                "reference IS NOT NULL AND reference <> '' AND status <> 'cancelled'"
            ),
        ),
        Index(
            "ix_inbound_shipments_org_sha",
            "organization_id",
            "document_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="warehouse", server_default=text("'warehouse'"), nullable=False
    )
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    booked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    booked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    organization: Mapped["Organization | None"] = relationship()
    booked_by_user: Mapped["User | None"] = relationship(foreign_keys=[booked_by])
    lines: Mapped[list["InboundShipmentLine"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class InboundShipmentLine(Base):
    __tablename__ = "inbound_shipment_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("inbound_shipments.id", ondelete="CASCADE")
    )
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    supplier_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)

    shipment: Mapped["InboundShipment"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()


class InboundUploadAttempt(Base):
    """One inbound document-processing attempt, including attempts that never book."""

    __tablename__ = "inbound_upload_attempts"
    __table_args__ = (
        Index("ix_inbound_uploads_org_created", "organization_id", "created_at"),
        Index("ix_inbound_uploads_org_sha", "organization_id", "document_sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    shipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("inbound_shipments.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    source_type: Mapped[str] = mapped_column(String(20), default="file")
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    error_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    bookable_line_count: Mapped[int] = mapped_column(Integer, default=0)
    booked_line_count: Mapped[int] = mapped_column(Integer, default=0)
    booked_quantity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()
    uploaded_by_user: Mapped["User | None"] = relationship(foreign_keys=[uploaded_by])
    shipment: Mapped["InboundShipment | None"] = relationship()


class SupplierSKUMapping(Base):
    __tablename__ = "supplier_sku_mappings"
    __table_args__ = (
        # sku_id is part of the key: one supplier code may legitimately carry
        # both the case and the loose-bottle product of the same wine. Inbound
        # offers the alternatives instead of overwriting the earlier link.
        UniqueConstraint(
            "organization_id",
            "supplier_name",
            "supplier_code",
            "sku_id",
            name="uq_supplier_sku_mapping_org_supplier_code_sku",
        ),
        Index(
            "uq_supplier_sku_mapping_global_supplier_code",
            "supplier_name",
            "supplier_code",
            "sku_id",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
            sqlite_where=text("organization_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    supplier_name: Mapped[str] = mapped_column(String(255))
    supplier_code: Mapped[str] = mapped_column(String(100))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()
    sku: Mapped["SKU"] = relationship()


class ProductAttribute(Base):
    """Defines an attribute type (kenmerk) for products within an organization.

    Examples: 'Druivensoort', 'Regio', 'Smaakprofiel', 'Allergenen'.
    """
    __tablename__ = "product_attributes"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship()
    values: Mapped[list["ProductAttributeValue"]] = relationship(
        back_populates="attribute", cascade="all, delete-orphan",
        order_by="ProductAttributeValue.sort_order, ProductAttributeValue.value",
    )


class ProductAttributeValue(Base):
    """A predefined allowed value (kenmerk waarde) for a product attribute.

    Examples for attribute 'Wijntype': 'Rood', 'Wit', 'Rosé', 'Mousseux'.
    """
    __tablename__ = "product_attribute_values"
    __table_args__ = (UniqueConstraint("attribute_id", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("product_attributes.id", ondelete="CASCADE")
    )
    value: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    attribute: Mapped["ProductAttribute"] = relationship(back_populates="values")


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint(
            "sku_id",
            "organization_id",
            "inventory_location",
            name="uq_inventory_balances_sku_org_location",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="warehouse", server_default=text("'warehouse'"), nullable=False
    )
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_movement_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    sku: Mapped["SKU"] = relationship()
    organization: Mapped["Organization | None"] = relationship()

    @property
    def quantity_available(self) -> int:
        return max(self.quantity_on_hand - self.quantity_reserved, 0)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="warehouse", server_default=text("'warehouse'"), nullable=False
    )
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL means an automated source-channel reconciliation rather than a human
    # inventory action.
    performed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sku: Mapped["SKU"] = relationship()
    organization: Mapped["Organization | None"] = relationship()
    performed_by_user: Mapped["User"] = relationship(foreign_keys=[performed_by])


class ChannelConnection(Base):
    """A sales-channel connection (Shopify/bol) for one organization.

    Holds the per-org operational state for importing channel orders: which
    channel, observe vs live mode, and the incremental sync cursor. Credentials
    are added when the adapter that needs them lands (PR 2). One connection per
    (organization, channel).
    """
    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "channel", name="uq_channel_conn_org_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20))
    # Filled by the OAuth install flow. shop_domain is the *.myshopify.com host;
    # the Admin API token is authenticated-encrypted with a server-side key that
    # never lives in this database. key_id versions the ciphertext format/key.
    shop_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cached primary location id at the shop, resolved once and reused as the
    # target of inventory write-back. NULL until first resolved.
    shopify_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Fulfillments already present before this moment belong to the physical
    # opening count and are baselined, not deducted again. Set once at go-live.
    inventory_authority_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # "observe" = import + show for reconciliation, no stock/fulfilment effect;
    # "live" = born-active + stock sync. Default observe — never act by
    # surprise.
    mode: Mapped[str] = mapped_column(
        String(20), default="observe", server_default="observe"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    # Incremental sync pointer (e.g. last processed order id / timestamp), so a
    # poll never replays the whole order history.
    cursor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship()


class ChannelSyncLog(Base):
    """One record per imported channel order — the data the observe/reconciliation
    view (PR 4) reads: did it import, how many lines matched a SKU, and which
    EANs did not match the catalogue."""
    __tablename__ = "channel_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(20))  # created / updated
    matched_lines: Mapped[int] = mapped_column(Integer, default=0)
    # JSON-encoded list of EANs on the order that had no matching SKU.
    unmatched_eans: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()


class Location(Base):
    """A physical pick location in the warehouse (row/cabinet/shelf).

    Managed by the courier (warehouse worker). ``code`` is the scannable barcode
    printed on the shelf; the pick flow verifies a scanned code against it before
    the products at that location may be booked. Locations are warehouse-global
    (``organization_id`` optional) because one courier operation serves multiple
    merchants from the same physical space; ``code`` is therefore unique globally.
    """
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    rij: Mapped[str | None] = mapped_column(String(20), nullable=True)
    kast: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plank: Mapped[str | None] = mapped_column(String(20), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sku_links: Mapped[list["SKULocation"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )


class SKULocation(Base):
    """Links a barcode SKU to a pick location (many-to-many).

    Only barcode products may be linked — vision/wine products are picked by
    photo, not by shelf, and stay out of this system entirely (enforced in the
    router). ``is_primary`` marks the main pick spot when a product lives in
    more than one place.
    """
    __tablename__ = "sku_locations"
    __table_args__ = (
        UniqueConstraint("sku_id", "location_id", name="uq_sku_locations_sku_location"),
        Index("ix_sku_locations_location_id", "location_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE")
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    sku: Mapped["SKU"] = relationship(back_populates="location_links")
    location: Mapped["Location"] = relationship(back_populates="sku_links")


class AdviceSale(Base):
    """One completed sale reported by the advice app (shop counter or webshop).

    Dockscan books these off stock immediately: unlike a channel order there is
    no pick step to wait for — the bottles left with the customer. The row
    exists to make the report idempotent, since a counter on bad wifi retries.
    """
    __tablename__ = "advice_sales"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "sale_id", name="uq_advice_sales_org_sale"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    # The advice app's own order id. Opaque here; only used to recognise a retry.
    sale_id: Mapped[str] = mapped_column(String(100))
    # Counter sales have already left the shop and therefore book the store
    # pool directly. Web pickup orders use AdviceReservation below instead.
    channel: Mapped[str] = mapped_column(String(20))
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="store", server_default=text("'store'"), nullable=False
    )
    occurred_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship()
    lines: Mapped[list["AdviceSaleLine"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )


class AdviceSaleLine(Base):
    """One booked product line of an advice sale.

    Unique per (sale, sku) so a retry re-reports the same line without booking
    it twice. A line the first call could not match — the product was not linked
    yet — is simply absent, and a later retry adds it.
    """
    __tablename__ = "advice_sale_lines"
    __table_args__ = (
        UniqueConstraint("sale_id", "sku_id", name="uq_advice_sale_lines_sale_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(
        ForeignKey("advice_sales.id", ondelete="CASCADE")
    )
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer)
    # SET NULL: force-deleting a SKU wipes its stock movements first, and the
    # sale line must survive that delete long enough for its own CASCADE to fire.
    stock_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movements.id", ondelete="SET NULL"), nullable=True
    )

    sale: Mapped["AdviceSale"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()


class AdviceReservation(Base):
    """Store-stock hold for one pickup order owned by wijnadvies1.

    It deliberately is not a Dockscan Order: pickup orders stay out of Scan &
    Boek. The source order id is the idempotency key for reserve, collect and
    release operations.
    """

    __tablename__ = "advice_reservations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "external_order_id",
            name="uq_advice_reservations_org_external_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    external_order_id: Mapped[str] = mapped_column(String(100))
    order_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fulfillment_method: Mapped[str] = mapped_column(
        String(20), default="pickup", server_default=text("'pickup'"), nullable=False
    )
    inventory_location: Mapped[str] = mapped_column(
        String(20), default="store", server_default=text("'store'"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    collected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped["Organization"] = relationship()
    lines: Mapped[list["AdviceReservationLine"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )


class AdviceReservationLine(Base):
    __tablename__ = "advice_reservation_lines"
    __table_args__ = (
        UniqueConstraint(
            "reservation_id", "sku_id", name="uq_advice_reservation_lines_reservation_sku"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("advice_reservations.id", ondelete="CASCADE")
    )
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer)

    reservation: Mapped["AdviceReservation"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()


class OrderDeliveryAddress(Base):
    """Where a delivery order must be shipped.

    A separate table rather than eight nullable columns on ``orders``: these are
    the only personal details Dockscan keeps about a webshop customer, and
    holding them apart means the retention question has one answer and one place
    to act on it. Only advice-app delivery orders have a row; Shopify and bol
    labels are printed at the channel, which keeps its own address.

    A snapshot, like the advice app's own copy. A customer who moves house must
    never rewrite the address of a parcel that already carries a label.
    """

    __tablename__ = "order_delivery_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    recipient_name: Mapped[str] = mapped_column(String(200))
    street: Mapped[str] = mapped_column(String(200))
    house_number: Mapped[str] = mapped_column(String(20))
    # Separate from ``house_number`` because carriers ask for them separately:
    # "12" plus "B" is a different delivery than "12B".
    house_number_suffix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(120))
    # ISO 3166-1 alpha-2, stored per order rather than an assumed "NL" so a first
    # delivery across the border needs no migration.
    country: Mapped[str] = mapped_column(
        String(2), default="NL", server_default=text("'NL'"), nullable=False
    )
    # The number the carrier calls at the door.
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship(back_populates="delivery_address")
