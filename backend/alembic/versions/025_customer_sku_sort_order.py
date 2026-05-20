"""Add sort_order to customer_skus

Revision ID: 025
Revises: 024
Create Date: 2026-05-20

Lets traders define a per-customer display order for assortment SKUs.
That order is reused on the customer order form. Existing rows are
seeded with positions based on the current alphabetical-by-SKU-name
order so the UI looks identical immediately after the migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer_skus",
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT cs.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY cs.customer_id
                           ORDER BY s.name, cs.id
                       ) AS rn
                FROM customer_skus cs
                JOIN skus s ON s.id = cs.sku_id
            )
            UPDATE customer_skus AS cs
            SET sort_order = ranked.rn
            FROM ranked
            WHERE cs.id = ranked.id
            """
        )
    )


def downgrade() -> None:
    op.drop_column("customer_skus", "sort_order")
