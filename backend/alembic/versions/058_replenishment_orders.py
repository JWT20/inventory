"""Let a merchant order stock for their own shop or webshop.

Everything arrives at the warehouse, but the shop and the webshop sell loose
bottles. Until now there was no way to ask the courier to move a box from the
warehouse onto one of those shelves: an order always belonged to a customer.

Two columns carry that distinction. ``order_kind`` separates a customer order
from a replenishment of the merchant's own stock, and ``destination_location``
records which pool the picked goods land in. Existing orders are customer
orders with no destination, which is exactly the old behaviour.

Revision ID: 058
Revises: 057
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "order_kind",
            sa.String(length=20),
            nullable=False,
            server_default="customer",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("destination_location", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "destination_location")
    op.drop_column("orders", "order_kind")
