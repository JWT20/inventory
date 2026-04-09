"""Add delivery_day to customers table

Revision ID: 017
Revises: 016
Create Date: 2026-04-09

Adds a delivery_day field (wednesday/thursday/friday) so each customer
can have a preferred delivery day for cross-docking. Defaults to thursday.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "delivery_day",
            sa.String(20),
            nullable=False,
            server_default="thursday",
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "delivery_day")
