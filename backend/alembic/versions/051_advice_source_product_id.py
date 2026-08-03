"""Link advice products to organization-scoped bottle SKUs.

Revision ID: 051
Revises: 050
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column("source_product_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "uq_skus_org_source_product_id",
        "skus",
        ["organization_id", "source_product_id"],
        unique=True,
        postgresql_where=sa.text("source_product_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_skus_advice_product_is_bottle",
        "skus",
        "source_product_id IS NULL OR is_bottle = true",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_skus_advice_product_is_bottle", "skus", type_="check"
    )
    op.drop_index("uq_skus_org_source_product_id", table_name="skus")
    op.drop_column("skus", "source_product_id")
