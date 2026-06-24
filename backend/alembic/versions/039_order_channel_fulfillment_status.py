"""Add orders.channel_fulfillment_status (fulfillment state at the source channel).

Channel imports now also keep the order's fulfillment state (Shopify
displayFulfillmentStatus, e.g. "fulfilled"/"unfulfilled"), refreshed on every
sync. The observe view surfaces it so already-shipped orders are visible, and
the later cutover uses it to keep fulfilled orders out of the pick list.

Revision ID: 039
Revises: 038
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("channel_fulfillment_status", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "channel_fulfillment_status")
