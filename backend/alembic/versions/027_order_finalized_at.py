"""Add finalized_at to orders table

Revision ID: 027
Revises: 026
Create Date: 2026-06-04

Records when an order first reached a terminal state (completed/closed) so the
monthly booked-boxes report can bucket boxes by the month the order was
finalized. Existing terminal orders are backfilled from updated_at (best
effort — these orders are no longer edited, so updated_at approximates the
finalize moment).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_orders_status_finalized_at",
        "orders",
        ["status", "finalized_at"],
    )
    # Backfill historical completed/closed orders.
    op.execute(
        """
        UPDATE orders
        SET finalized_at = updated_at
        WHERE status IN ('completed', 'closed')
          AND finalized_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_orders_status_finalized_at", table_name="orders")
    op.drop_column("orders", "finalized_at")
