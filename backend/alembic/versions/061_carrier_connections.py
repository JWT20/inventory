"""Give every organization its own shipping-carrier account.

The Veloyd account Dockscan talks to belongs to the carrier, who keeps a client
account per merchant. Until now the key lived in the environment, so one
merchant's key served the whole process — which would have printed a second
merchant's parcels under the first one's sender address and invoice.

No data is migrated. An organization without a row keeps falling back to the
environment key, so the merchant that was configured that way keeps shipping
until its own key is stored.

Revision ID: 061
Revises: 060
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "carrier_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("carrier", sa.String(length=20), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_key_id", sa.String(length=32), nullable=True),
        sa.Column("base_url", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "carrier", name="uq_carrier_conn_org_carrier"
        ),
    )


def downgrade() -> None:
    op.drop_table("carrier_connections")
