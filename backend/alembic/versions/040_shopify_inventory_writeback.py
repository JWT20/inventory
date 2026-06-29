"""Add Shopify inventory write-back mapping columns.

App-leidende voorraadsync: lokale mutaties (pick/undo/aanpassing/reservering)
worden teruggeduwd naar Shopify als absolute ``available``. Daarvoor cachen we
per SKU het Shopify ``inventory_item_id`` (opgelost via de variant-barcode/EAN)
en per connectie de primaire ``location_id``.

Revision ID: 040
Revises: 039
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column("shopify_inventory_item_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "channel_connections",
        sa.Column("shopify_location_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channel_connections", "shopify_location_id")
    op.drop_column("skus", "shopify_inventory_item_id")
