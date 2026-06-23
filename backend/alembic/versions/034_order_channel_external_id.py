"""Add orders.channel and orders.external_id (order provenance + dedup).

channel records where an order came from: "manual" (created in-app or by a
customer), "shopify" or "bol". external_id holds the source channel's order id
so the same external order is never imported twice (unique per organization +
channel; manual orders have a NULL external_id and are excluded).

Provenance only — the order lifecycle (whether an order is born active or awaits
merchant approval) is decided by the organization's modules, not by channel.

Revision ID: 034
Revises: 033
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills every existing row as "manual"; all current orders
    # were created in-app or via the customer portal.
    op.add_column(
        "orders",
        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column("orders", sa.Column("external_id", sa.String(length=255), nullable=True))

    # Unique per (organization, channel); NULL external_ids (manual orders) are
    # excluded so they all coexist.
    op.create_index(
        "uq_orders_org_channel_external",
        "orders",
        ["organization_id", "channel", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
        sqlite_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_org_channel_external", table_name="orders")
    op.drop_column("orders", "external_id")
    op.drop_column("orders", "channel")
