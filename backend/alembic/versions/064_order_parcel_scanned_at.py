"""Remember which boxes of an order have been scanned onto the van.

An order of twelve bottles leaves as two boxes with two labels. Shipping it the
moment one of them is scanned would let the second travel unchecked, which is
the one thing the shipping-label gate exists to prevent. So each box records
its own scan, and the order ships when none is left.

Revision ID: 064
Revises: 063
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_parcels", sa.Column("scanned_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("order_parcels", "scanned_at")
