"""Record how much of an advice hold a pick has already taken.

A delivery order's bottles are held the moment the customer pays, and taken off
the shelf when the courier picks them. Both events touch the same reservation,
so the hold has to remember what is already gone: without it a pick would free
stock the hold no longer covers, and a later collect or release would give back
bottles that already left the building.

Existing holds have consumed nothing — nothing was pickable before this.

Revision ID: 060
Revises: 059
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "advice_reservation_lines",
        sa.Column(
            "consumed_quantity",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("advice_reservation_lines", "consumed_quantity")
