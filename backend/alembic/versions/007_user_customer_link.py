"""Add customer_id FK to users table for linking customer-role users to a Customer record.

Revision ID: 007
Revises: 006
Create Date: 2026-03-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "customer_id")
