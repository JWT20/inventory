"""Backfill inbound concept products from category 'other' to 'wine'.

Concept SKUs created by the inbound scan used to be stored as category
'other' (a generic-product placeholder). They are really unfinished wines, so
new ones are now created as 'wine'. This converts the ones that already exist
so the old "Overig" behaviour cannot resurface for them.

Inbound concepts are identified by their source=inbound_scan attribute, so
genuine non-wine products (if any) are left untouched.

Revision ID: 031
Revises: 030
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE skus SET category = 'wine'
        WHERE category = 'other'
          AND id IN (
            SELECT sku_id FROM sku_attributes
            WHERE key = 'source' AND value = 'inbound_scan'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE skus SET category = 'other'
        WHERE category = 'wine'
          AND id IN (
            SELECT sku_id FROM sku_attributes
            WHERE key = 'source' AND value = 'inbound_scan'
          )
        """
    )
