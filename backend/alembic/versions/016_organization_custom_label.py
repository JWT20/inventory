"""Add custom_label to organizations table

Revision ID: 016
Revises: 015
Create Date: 2026-04-08

Adds an optional custom_label field so each organization can override
the default 'Magazijn' branding shown in the UI header.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("custom_label", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "custom_label")
