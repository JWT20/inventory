"""Add discount_percentage to customers table

Revision ID: 014
Revises: 013
Create Date: 2026-04-08

Adds a customer-level discount percentage that applies to all products
for that customer. Wine-specific discounts (in customer_skus) take
precedence over this default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("discount_percentage", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customers", "discount_percentage")
