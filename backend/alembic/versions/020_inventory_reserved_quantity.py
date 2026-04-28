"""Add reserved quantity to inventory balances

Revision ID: 020
Revises: 019
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_balances",
        sa.Column(
            "quantity_reserved",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("inventory_balances", "quantity_reserved")
