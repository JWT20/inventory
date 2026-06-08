"""Add possible delivery days to customers

Revision ID: 028
Revises: 027
Create Date: 2026-06-08

Customers can now have multiple possible delivery days. Existing customers are
initialized with the standard Wednesday/Thursday/Friday options.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_DELIVERY_DAYS = '["wednesday", "thursday", "friday"]'


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "delivery_days",
            sa.Text(),
            nullable=False,
            server_default=DEFAULT_DELIVERY_DAYS,
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "delivery_days")
