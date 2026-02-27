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

# text-embedding-3-small produces 1536-dimensional vectors
EMBEDDING_DIM = 1536


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(
        String(20), default="picker"
    )  # admin, picker, viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


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
    order_lines: Mapped[list["OrderLine"]] = relationship(back_populates="sku")


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


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, picking, completed
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    picked_quantity: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, picked

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship(back_populates="order_lines")
    pick_logs: Mapped[list["PickLog"]] = relationship(
        back_populates="order_line", cascade="all, delete-orphan"
    )


class PickLog(Base):
    __tablename__ = "pick_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_line_id: Mapped[int] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE")
    )
    matched_sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("skus.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    correct: Mapped[bool] = mapped_column(Boolean)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    order_line: Mapped["OrderLine"] = relationship(back_populates="pick_logs")
    matched_sku: Mapped["SKU | None"] = relationship()
