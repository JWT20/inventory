"""initial schema with users table

Revision ID: 001
Revises:
Create Date: 2026-02-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="picker", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skus_sku_code", "skus", ["sku_code"], unique=True)

    op.create_table(
        "reference_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sku_id",
            sa.Integer(),
            sa.ForeignKey("skus.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("vision_description", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_number", sa.String(50), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)

    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("picked_quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
    )

    op.create_table(
        "pick_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_line_id",
            sa.Integer(),
            sa.ForeignKey("order_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matched_sku_id",
            sa.Integer(),
            sa.ForeignKey("skus.id"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("image_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pick_logs")
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("reference_images")
    op.drop_table("skus")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
