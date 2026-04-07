"""Product attribute definitions: kenmerken en kenmerk waardes

Revision ID: 011
Revises: 010
Create Date: 2026-04-04

Adds product_attributes (attribute definitions per organization) and
product_attribute_values (allowed values per attribute) tables.
These let organizations define their own attribute catalog with
predefined allowed values, separate from the existing sku_attributes
key-value store that holds actual SKU data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Product attribute definitions (kenmerken)
    op.create_table(
        "product_attributes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("organization_id", "name"),
    )
    op.create_index(
        "ix_product_attributes_org_id",
        "product_attributes",
        ["organization_id"],
    )

    # 2. Allowed values per attribute (kenmerk waardes)
    op.create_table(
        "product_attribute_values",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "attribute_id",
            sa.Integer,
            sa.ForeignKey("product_attributes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("attribute_id", "value"),
    )
    op.create_index(
        "ix_product_attribute_values_attr_id",
        "product_attribute_values",
        ["attribute_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_attribute_values_attr_id",
        table_name="product_attribute_values",
    )
    op.drop_table("product_attribute_values")
    op.drop_index(
        "ix_product_attributes_org_id",
        table_name="product_attributes",
    )
    op.drop_table("product_attributes")
