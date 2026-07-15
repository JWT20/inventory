"""Track exact channel-line fulfillment and the inventory authority cutover.

Revision ID: 043
Revises: 042
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channel_connections",
        sa.Column("inventory_authority_started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "order_lines", sa.Column("channel_line_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "order_lines", sa.Column("channel_current_quantity", sa.Integer(), nullable=True)
    )
    op.add_column(
        "order_lines",
        sa.Column("channel_unfulfilled_quantity", sa.Integer(), nullable=True),
    )
    op.add_column(
        "order_lines",
        sa.Column(
            "channel_fulfilled_seen",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "order_lines",
        sa.Column(
            "channel_fulfilled_from_app",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "order_lines",
        sa.Column(
            "channel_fulfilled_external",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.alter_column("stock_movements", "performed_by", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM stock_movements WHERE performed_by IS NULL")
    op.alter_column("stock_movements", "performed_by", nullable=False)
    op.drop_column("order_lines", "channel_fulfilled_external")
    op.drop_column("order_lines", "channel_fulfilled_from_app")
    op.drop_column("order_lines", "channel_fulfilled_seen")
    op.drop_column("order_lines", "channel_unfulfilled_quantity")
    op.drop_column("order_lines", "channel_current_quantity")
    op.drop_column("order_lines", "channel_line_id")
    op.drop_column("channel_connections", "inventory_authority_started_at")
