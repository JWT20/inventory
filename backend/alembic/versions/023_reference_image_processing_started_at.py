"""Add reference_images.processing_started_at

Revision ID: 023
Revises: 022
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reference_images",
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reference_images", "processing_started_at")
