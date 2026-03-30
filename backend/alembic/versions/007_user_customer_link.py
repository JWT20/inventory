"""Add customer_id FK to users table and fix order_lines FK ondelete.

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
    # Add customer_id to users for linking customer-role users
    op.add_column(
        "users",
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True),
    )

    # Fix order_lines.customer_id FK to SET NULL on delete (was RESTRICT)
    op.drop_constraint(
        "order_lines_customer_id_fkey", "order_lines", type_="foreignkey"
    )
    op.create_foreign_key(
        "order_lines_customer_id_fkey",
        "order_lines",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Restore original FK without ondelete
    op.drop_constraint(
        "order_lines_customer_id_fkey", "order_lines", type_="foreignkey"
    )
    op.create_foreign_key(
        "order_lines_customer_id_fkey",
        "order_lines",
        "customers",
        ["customer_id"],
        ["id"],
    )
    op.drop_column("users", "customer_id")
