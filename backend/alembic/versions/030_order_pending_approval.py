"""Replace the draft order status with pending_approval.

Customer-created orders now await merchant approval instead of starting as
draft. Existing draft orders keep their delivery_week (already assigned via
the old deadline logic) and only get the new status name.

Revision ID: 030
Revises: 029
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE orders SET status = 'pending_approval' WHERE status = 'draft'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE orders SET status = 'draft' WHERE status = 'pending_approval'"
    )
