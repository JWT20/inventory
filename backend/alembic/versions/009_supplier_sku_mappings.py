"""Add supplier SKU mappings and supplier_code on inbound shipment lines.

Revision ID: 009
Revises: 008
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_shipment_lines",
        sa.Column("supplier_code", sa.String(length=100), nullable=True),
    )

    op.create_table(
        "supplier_sku_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=False),
        sa.Column("supplier_code", sa.String(length=100), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "supplier_name",
            "supplier_code",
            name="uq_supplier_sku_mapping_org_supplier_code",
        ),
    )
    # Enforce uniqueness for global mappings where organization_id IS NULL
    op.create_index(
        "uq_supplier_sku_mapping_global_supplier_code",
        "supplier_sku_mappings",
        ["supplier_name", "supplier_code"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
        sqlite_where=sa.text("organization_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_supplier_sku_mapping_global_supplier_code", table_name="supplier_sku_mappings")
    op.drop_table("supplier_sku_mappings")
    op.drop_column("inbound_shipment_lines", "supplier_code")
