"""Keep the advice app's commercial flag apart from skus.active.

A linked bottle is only sellable when the advice app wants it *and* Dockscan
has a usable reference image, so the feed's own answer needs its own column;
writing it into `active` would lose it the moment the image rule lowers that.

Revision ID: 052
Revises: 051
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column("source_active", sa.Boolean(), nullable=True),
    )
    # Bottles linked before this migration (manually or by an earlier sync run)
    # keep the availability the merchant sees today. Without this backfill the
    # NULL would read as "not available" and deactivate live products until the
    # next snapshot arrives.
    op.execute(
        "UPDATE skus SET source_active = active "
        "WHERE source_product_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("skus", "source_active")
