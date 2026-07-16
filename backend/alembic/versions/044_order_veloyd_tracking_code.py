"""Store the unique Veloyd tracking barcode on an order.

Revision ID: 044
Revises: 043
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("veloyd_tracking_code", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_orders_veloyd_tracking_code",
        "orders",
        ["veloyd_tracking_code"],
        unique=True,
        postgresql_where=sa.text("veloyd_tracking_code IS NOT NULL"),
        sqlite_where=sa.text("veloyd_tracking_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_veloyd_tracking_code", table_name="orders")
    op.drop_column("orders", "veloyd_tracking_code")
