"""Add Web Push subscriptions and transactional delivery outbox.

Revision ID: 050
Revises: 049
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )
    op.create_index(
        "ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"]
    )

    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["push_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_key",
            "subscription_id",
            name="uq_push_delivery_event_subscription",
        ),
    )
    op.create_index(
        "ix_push_deliveries_pending",
        "push_deliveries",
        ["sent_at", "failed_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_push_deliveries_pending", table_name="push_deliveries")
    op.drop_table("push_deliveries")
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
