"""Normalize supplier mapping keys and deduplicate case collisions.

Revision ID: 010
Revises: 009
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize values to canonical keys.
    op.execute(
        sa.text(
            """
            UPDATE supplier_sku_mappings
            SET supplier_name = UPPER(TRIM(supplier_name)),
                supplier_code = UPPER(TRIM(supplier_code))
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE inbound_shipment_lines
            SET supplier_code = UPPER(TRIM(supplier_code))
            WHERE supplier_code IS NOT NULL
            """
        )
    )

    # Remove duplicates caused by case/whitespace collisions after normalization.
    op.execute(
        sa.text(
            """
            DELETE FROM supplier_sku_mappings
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY organization_id, supplier_name, supplier_code
                               ORDER BY updated_at DESC, id DESC
                           ) AS rn
                    FROM supplier_sku_mappings
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )


def downgrade() -> None:
    # Irreversible data normalization; keep values as-is.
    pass
