"""Add orders.ordered_at (source-channel order timestamp).

Channel imports parse the order's timestamp at the source (Shopify createdAt);
storing it lets the reconciliation view sort/show by the real order date instead
of the import moment (created_at).

Revision ID: 037
Revises: 036
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("ordered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "ordered_at")
