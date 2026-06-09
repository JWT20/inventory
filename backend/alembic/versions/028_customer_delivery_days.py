"""Add possible delivery days to customers

Revision ID: 028
Revises: 027
Create Date: 2026-06-08

Customers can now have multiple possible delivery days. Existing customers are
initialized so that their current delivery day always remains selectable:
standard customers get Wednesday/Thursday/Friday, while early-week
(monday/tuesday) customers keep the full set so their own day isn't dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STANDARD_DELIVERY_DAYS = '["wednesday", "thursday", "friday"]'
ALL_DELIVERY_DAYS = '["monday", "tuesday", "wednesday", "thursday", "friday"]'


def upgrade() -> None:
    # server_default backfills every existing row with the standard set.
    op.add_column(
        "customers",
        sa.Column(
            "delivery_days",
            sa.Text(),
            nullable=False,
            server_default=STANDARD_DELIVERY_DAYS,
        ),
    )
    # Early-week (monday/tuesday) customers — e.g. café de sigaar — could
    # previously order on any day. The standard backfill would have dropped
    # their monday/tuesday option, so re-expand them to the full set; this
    # guarantees their preferred delivery_day stays a valid possible day.
    op.execute(
        "UPDATE customers SET delivery_days = '"
        + ALL_DELIVERY_DAYS
        + "' WHERE delivery_day IN ('monday', 'tuesday')"
    )


def downgrade() -> None:
    op.drop_column("customers", "delivery_days")
