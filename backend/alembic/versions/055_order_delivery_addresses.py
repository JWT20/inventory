"""Keep the shipping address of an advice-app delivery order.

A table of its own rather than columns on ``orders``: this is the only personal
data Dockscan holds about a webshop customer, and keeping it apart means one
place to purge when a retention term expires.

Revision ID: 055
Revises: 054
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_delivery_addresses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("recipient_name", sa.String(length=200), nullable=False),
        sa.Column("street", sa.String(length=200), nullable=False),
        sa.Column("house_number", sa.String(length=20), nullable=False),
        sa.Column("house_number_suffix", sa.String(length=20), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column(
            "country",
            sa.String(length=2),
            server_default=sa.text("'NL'"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One address per order: a second row would make "where does this parcel
        # go" ambiguous at exactly the moment it matters.
        sa.UniqueConstraint("order_id", name="uq_order_delivery_addresses_order"),
    )


def downgrade() -> None:
    op.drop_table("order_delivery_addresses")
