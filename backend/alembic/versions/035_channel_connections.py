"""Add channel_connections and channel_sync_logs (Fase 3 observe-mode fundament).

channel_connections holds per-org sales-channel state (channel, observe/live
mode, sync cursor). channel_sync_logs records each imported channel order with
how many lines matched a SKU and which EANs did not — the data the observe /
reconciliation view reads. No order-status CHECK constraint exists, so the new
"observed" order status needs no schema change.

Revision ID: 035
Revises: 034
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column(
            "mode", sa.String(length=20), nullable=False, server_default="observe"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("cursor", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "organization_id", "channel", name="uq_channel_conn_org_channel"
        ),
    )

    op.create_table(
        "channel_sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("matched_lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_eans", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_channel_sync_logs_org_channel",
        "channel_sync_logs",
        ["organization_id", "channel"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_sync_logs_org_channel", table_name="channel_sync_logs")
    op.drop_table("channel_sync_logs")
    op.drop_table("channel_connections")
