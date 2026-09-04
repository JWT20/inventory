"""Let one delivery order leave the building as several boxes.

A case of twelve bottles is two boxes with two labels, and both have to be
scanned before the order may ship — which the single tracking-code column on
``orders`` cannot express. Only parcels Dockscan creates itself live here;
Shopify and bol parcels are made by Veloyd's own webshop links and keep
learning their one code on the order.

``tracking_code`` is nullable because Veloyd assigns the track-and-trace value
when the carrier prints the label, not when the parcel is created.

Revision ID: 062
Revises: 061
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_parcels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("veloyd_parcel_id", sa.String(length=64), nullable=False),
        sa.Column("tracking_code", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("veloyd_parcel_id"),
        sa.UniqueConstraint("order_id", "sequence", name="uq_order_parcel_sequence"),
    )
    op.create_index(
        "uq_order_parcels_tracking_code",
        "order_parcels",
        ["tracking_code"],
        unique=True,
        postgresql_where=sa.text("tracking_code IS NOT NULL"),
    )
    op.add_column(
        "carrier_connections",
        sa.Column(
            "bottles_per_box",
            sa.Integer(),
            server_default="6",
            nullable=False,
        ),
    )
    op.add_column(
        "order_delivery_addresses",
        sa.Column("email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_delivery_addresses", "email")
    op.drop_column("carrier_connections", "bottles_per_box")
    op.drop_index("uq_order_parcels_tracking_code", table_name="order_parcels")
    op.drop_table("order_parcels")
