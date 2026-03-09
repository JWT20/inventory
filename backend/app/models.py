import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# gemini-embedding-001 default full-fidelity output
EMBEDDING_DIM = 3072


VALID_ROLES = ("admin", "merchant", "courier")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="courier")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_manage_products(self) -> bool:
        return self.role in ("admin", "merchant")


class SKU(Base):
    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    reference_images: Mapped[list["ReferenceImage"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )


class ReferenceImage(Base):
    __tablename__ = "reference_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"))
    image_path: Mapped[str] = mapped_column(String(500))
    vision_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sku: Mapped["SKU"] = relationship(back_populates="reference_images")


VALID_ORDER_STATUSES = ("draft", "pending_images", "active", "completed", "cancelled")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reference: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    merchant: Mapped["User"] = relationship()
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
    quantity: Mapped[int] = mapped_column(Integer)
    booked_count: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()


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
