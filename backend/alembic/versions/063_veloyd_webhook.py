"""Let Veloyd report a printed label instead of waiting for the first scan.

The carrier prints, and only then does Veloyd assign a track-and-trace value.
Until now Dockscan learned that value from the courier's scan, which is late:
the customer's mail is already out, and the parcel already stopped being
cancellable.

Veloyd's webhook field carries no authentication, so the secret lives in the
path. Only its digest is stored — a leaked database must not yield a working
URL.

Revision ID: 063
Revises: 062
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_parcels", sa.Column("tracking_url", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "order_parcels", sa.Column("label_printed_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "carrier_connections",
        sa.Column("webhook_token_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_carrier_conn_webhook_token",
        "carrier_connections",
        ["webhook_token_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_carrier_conn_webhook_token", "carrier_connections", type_="unique"
    )
    op.drop_column("carrier_connections", "webhook_token_hash")
    op.drop_column("order_parcels", "label_printed_at")
    op.drop_column("order_parcels", "tracking_url")
