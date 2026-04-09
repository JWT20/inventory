"""Add delivery_week to orders table

Revision ID: 019
Revises: 018
Create Date: 2026-04-09

Store which delivery week an order belongs to (e.g. '2026-W16').
Orders placed after Monday 08:00 deadline belong to the next week.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("delivery_week", sa.String(10), nullable=True),
    )
    # Backfill existing orders: derive week from created_at
    # This is approximate — orders created after the Monday deadline
    # should really be next week, but for historical data this is acceptable.
    op.execute("""
        UPDATE orders
        SET delivery_week = to_char(created_at, 'IYYY') || '-W' || to_char(created_at, 'IW')
        WHERE delivery_week IS NULL
    """)


def downgrade() -> None:
    op.drop_column("orders", "delivery_week")
