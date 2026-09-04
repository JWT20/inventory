"""Let Veloyd prove it is Veloyd with something other than the URL.

The webhook's credential was the path, and a path travels through proxy logs,
browser history and screenshots more easily than a header does. Veloyd can send
an Authorization header of our choosing; this stores what to expect.

Nullable on purpose: an organization without a stored value keeps working on the
path alone, so the two can be switched over one at a time instead of both at
once.

Revision ID: 065
Revises: 064
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "carrier_connections",
        sa.Column("webhook_auth_token_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("carrier_connections", "webhook_auth_token_hash")
