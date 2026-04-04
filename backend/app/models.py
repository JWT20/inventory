import datetime
import json

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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
VALID_SHIPMENT_STATUSES = ("draft", "booked")
VALID_MOVEMENT_TYPES = ("receive", "pick", "adjust", "count")
VALID_DISCOUNT_TYPES = ("percentage", "fixed")


class Organization(Base):
    """A merchant organization (e.g. 'Wijnhandel De Druif')."""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    enabled_modules: Mapped[str] = mapped_column(
        Text, default='["inventory","orders"]'
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

    @property
    def is_admin(self) -> bool:
        return self.is_platform_admin

    @property
    def can_manage_products(self) -> bool:
        return self.is_platform_admin or self.role in ("owner", "member")


class SKU(Base):
    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    default_price: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )

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
    __table_args__ = (UniqueConstraint("sku_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(500))

    sku: Mapped["SKU"] = relationship(back_populates="attributes")


class ReferenceImage(Base):
    __tablename__ = "reference_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    image_path: Mapped[str] = mapped_column(String(500))
    vision_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(20), default="done"
    )  # "pending", "processing", "done", "failed"
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
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()
    sku_links: Mapped[list["CustomerSKU"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerSKU(Base):
    __tablename__ = "customer_skus"
    __table_args__ = (UniqueConstraint("customer_id", "sku_id"),)

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

    customer: Mapped["Customer"] = relationship(back_populates="sku_links")
    sku: Mapped["SKU"] = relationship()


VALID_ORDER_STATUSES = ("draft", "pending_images", "active", "completed", "cancelled")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reference: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization | None"] = relationship()
    creator: Mapped["User | None"] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


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

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()
    customer: Mapped["Customer | None"] = relationship()

    @property
    def customer_name(self) -> str:
        if self.customer:
            return self.customer.name
        return self.klant


class Booking(Base):
    """1 scan = 1 box = 1 booking."""
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
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


class SupplierSKUMapping(Base):
    __tablename__ = "supplier_sku_mappings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "supplier_name",
            "supplier_code",
            name="uq_supplier_sku_mapping_org_supplier_code",
        ),
        Index(
            "uq_supplier_sku_mapping_global_supplier_code",
            "supplier_name",
            "supplier_code",
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
    __table_args__ = (UniqueConstraint("sku_id", "organization_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    last_movement_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    sku: Mapped["SKU"] = relationship()
    organization: Mapped["Organization | None"] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    performed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sku: Mapped["SKU"] = relationship()
    organization: Mapped["Organization | None"] = relationship()
    performed_by_user: Mapped["User"] = relationship(foreign_keys=[performed_by])
