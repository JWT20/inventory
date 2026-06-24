"""Add orders.channel_reference (human order number at the source channel).

Channel imports now keep the order's human number (Shopify order name, e.g.
"1262") next to the internal external_id. This is the reference the courier's
shipping label (Veloyd) carries, so storing it lets the observe view show it and
the later label-scan pick step match on it.

Revision ID: 038
Revises: 037
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("channel_reference", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "channel_reference")
