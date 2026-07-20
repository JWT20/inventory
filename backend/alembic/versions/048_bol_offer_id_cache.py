"""Cache the bol offer id on organization-scoped SKUs.

Revision ID: 048
Revises: 047
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column("bol_offer_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skus", "bol_offer_id")
