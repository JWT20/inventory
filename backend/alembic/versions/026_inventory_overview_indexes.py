"""Add indexes for inventory overview

Revision ID: 026
Revises: 025
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_skus_org_active_name",
        "skus",
        ["organization_id", "active", "name"],
    )
    op.create_index(
        "ix_sku_attributes_key_value_sku_id",
        "sku_attributes",
        ["key", "value", "sku_id"],
    )
    op.create_index(
        "ix_reference_images_sku_status_created",
        "reference_images",
        ["sku_id", "processing_status", "created_at"],
    )
    op.create_index("ix_customer_skus_sku_id", "customer_skus", ["sku_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_skus_sku_id", table_name="customer_skus")
    op.drop_index(
        "ix_reference_images_sku_status_created",
        table_name="reference_images",
    )
    op.drop_index(
        "ix_sku_attributes_key_value_sku_id",
        table_name="sku_attributes",
    )
    op.drop_index("ix_skus_org_active_name", table_name="skus")
