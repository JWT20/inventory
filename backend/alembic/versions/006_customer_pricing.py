"""Add pricing columns: default_price on skus, unit_price on customer_skus.

Revision ID: 006
Revises: 005
Create Date: 2026-03-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column("default_price", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "customer_skus",
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customer_skus", "unit_price")
    op.drop_column("skus", "default_price")
