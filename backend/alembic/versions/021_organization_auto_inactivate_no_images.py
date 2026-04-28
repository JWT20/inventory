"""Add auto_inactivate_no_images flag to organizations

Revision ID: 021
Revises: 020
Create Date: 2026-04-28

When true, the organization's SKUs are automatically marked inactive
whenever they have no usable reference image. The flag is purely
informational for ordering — restaurants can still order inactive SKUs
via their CustomerSKU assignment — but the warehouse scan flow refuses
to match against SKUs without a reference image (matching has nothing
to compare against).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "auto_inactivate_no_images",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "auto_inactivate_no_images")
