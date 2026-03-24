"""baseline schema

Revision ID: 001
Revises:
Create Date: 2026-03-24

Creates the full schema from scratch for fresh installs.
On existing databases, stamp this revision without running it:
    alembic stamp 001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("email", sa.String(320), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="courier"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "skus",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku_code", sa.String(50), unique=True, index=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("producent", sa.String(150), nullable=True),
        sa.Column("wijnaam", sa.String(150), nullable=True),
        sa.Column("wijntype", sa.String(50), nullable=True),
        sa.Column("jaargang", sa.String(10), nullable=True),
        sa.Column("volume", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "reference_images",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku_id", sa.Integer, sa.ForeignKey("skus.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("vision_description", sa.Text, nullable=True),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="done"),
        sa.Column("description_quality", sa.String(10), nullable=True),
        sa.Column("wine_check_overridden", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    # pgvector column — use raw SQL since Alembic doesn't natively handle vector types
    op.execute("ALTER TABLE reference_images ADD COLUMN embedding vector(3072)")

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), unique=True, index=True, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "customer_skus",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku_id", sa.Integer, sa.ForeignKey("skus.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("customer_id", "sku_id"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("merchant_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reference", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku_id", sa.Integer, sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("klant", sa.String(150), nullable=False, server_default=""),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("booked_count", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_line_id", sa.Integer, sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku_id", sa.Integer, sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("scanned_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scan_image_path", sa.String(500), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("customer_skus")
    op.drop_table("customers")
    op.drop_table("reference_images")
    op.drop_table("skus")
    op.drop_table("users")
