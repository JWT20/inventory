"""Add reference image processing error details

Revision ID: 022
Revises: 021
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reference_images",
        sa.Column("processing_error_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "reference_images",
        sa.Column("processing_error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "reference_images",
        sa.Column("duplicate_sku_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reference_images", "duplicate_sku_id")
    op.drop_column("reference_images", "processing_error_message")
    op.drop_column("reference_images", "processing_error_code")
