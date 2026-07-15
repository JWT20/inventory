"""Pick locations for barcode products.

Adds a warehouse pick-location table (row/cabinet/shelf, with a scannable code)
and a many-to-many link to barcode SKUs. Vision/wine products are never linked
(enforced in the locations router). Managed by the courier.

Revision ID: 041
Revises: 040
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("rij", sa.String(length=20), nullable=True),
        sa.Column("kast", sa.String(length=20), nullable=True),
        sa.Column("plank", sa.String(length=20), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locations_code", "locations", ["code"], unique=True)

    op.create_table(
        "sku_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_primary", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sku_id", "location_id", name="uq_sku_locations_sku_location"
        ),
    )
    op.create_index(
        "ix_sku_locations_location_id", "sku_locations", ["location_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sku_locations_location_id", table_name="sku_locations")
    op.drop_table("sku_locations")
    op.drop_index("ix_locations_code", table_name="locations")
    op.drop_table("locations")
