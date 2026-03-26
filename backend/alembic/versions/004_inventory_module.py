"""Add inventory module: shipments, balances, stock movements.

Revision ID: 004
Revises: 003
Create Date: 2026-03-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Inbound shipments (pakbon)
    op.create_table(
        "inbound_shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("supplier_name", sa.String(255), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("booked_at", sa.DateTime(), nullable=True),
        sa.Column("booked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )

    op.create_table(
        "inbound_shipment_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "shipment_id",
            sa.Integer(),
            sa.ForeignKey("inbound_shipments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
    )

    # Inventory balances
    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_movement_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sku_id", "merchant_id"),
    )

    # Stock movements (immutable ledger)
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(20), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("performed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_index("ix_stock_movements_sku_merchant", "stock_movements", ["sku_id", "merchant_id"])
    op.create_index("ix_stock_movements_created_at", "stock_movements", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_stock_movements_created_at", table_name="stock_movements")
    op.drop_index("ix_stock_movements_sku_merchant", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_table("inventory_balances")
    op.drop_table("inbound_shipment_lines")
    op.drop_table("inbound_shipments")
