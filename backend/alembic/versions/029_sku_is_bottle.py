"""Add skus.is_bottle flag (bottle products ordered per single bottle).

Revision ID: 029
Revises: 028
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills every existing row as a box product.
    op.add_column(
        "skus",
        sa.Column(
            "is_bottle",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("skus", "is_bottle")
