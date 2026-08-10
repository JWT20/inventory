"""Record advice-app sales so they book off stock idempotently.

Revision ID: 053
Revises: 052
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "advice_sales",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "sale_id", name="uq_advice_sales_org_sale"
        ),
    )
    op.create_table(
        "advice_sale_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_movement_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sale_id"], ["advice_sales.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"]),
        sa.ForeignKeyConstraint(["stock_movement_id"], ["stock_movements.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sale_id", "sku_id", name="uq_advice_sale_lines_sale_sku"),
    )


def downgrade() -> None:
    op.drop_table("advice_sale_lines")
    op.drop_table("advice_sales")
