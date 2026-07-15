"""Persist the operational reason for a parked channel order.

Revision ID: 042
Revises: 041
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("review_reason", sa.String(length=40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "review_reason")
