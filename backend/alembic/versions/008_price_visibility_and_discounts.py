"""Add price visibility to customers and discount columns to customer_skus.

Revision ID: 008
Revises: 007
Create Date: 2026-03-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Customer-level: price visibility toggle (default: show prices)
    op.add_column(
        "customers",
        sa.Column("show_prices", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )

    # Product-level (per customer-SKU): discount
    op.add_column(
        "customer_skus",
        sa.Column("discount_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "customer_skus",
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customer_skus", "discount_value")
    op.drop_column("customer_skus", "discount_type")
    op.drop_column("customers", "show_prices")
