"""Add remarks column to orders table

Revision ID: 014
Revises: 013
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("remarks", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("orders", "remarks")
