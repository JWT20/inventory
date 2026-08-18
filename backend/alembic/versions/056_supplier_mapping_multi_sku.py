"""Allow one supplier code to point at more than one product.

A wine that arrives under a single supplier article number can live in the
catalog twice on purpose: once as the case that goes to the warehouse, once as
the loose bottle that goes to the shop shelf and the advice app. The old
uniqueness (organization, supplier_name, supplier_code) allowed only one of the
two, so learning the second link silently overwrote the first — and inbound had
to be relinked by hand every time the other unit showed up.

Widen the key with sku_id: a code may now carry several products, and inbound
offers them as a choice instead of guessing.

Revision ID: 056
Revises: 055
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_sku_mappings") as batch_op:
        batch_op.drop_constraint(
            "uq_supplier_sku_mapping_org_supplier_code", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_supplier_sku_mapping_org_supplier_code_sku",
            ["organization_id", "supplier_name", "supplier_code", "sku_id"],
        )
    op.drop_index("uq_supplier_sku_mapping_global_supplier_code")
    op.create_index(
        "uq_supplier_sku_mapping_global_supplier_code",
        "supplier_sku_mappings",
        ["supplier_name", "supplier_code", "sku_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
        sqlite_where=sa.text("organization_id IS NULL"),
    )


def downgrade() -> None:
    # Narrowing back to one product per code has to drop the extra links; keep
    # the most recently used one, which is what auto-matching would pick.
    op.execute(
        """
        DELETE FROM supplier_sku_mappings
        WHERE id NOT IN (
            SELECT keep_id FROM (
                SELECT id AS keep_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY organization_id, supplier_name, supplier_code
                           ORDER BY updated_at DESC, id DESC
                       ) AS rn
                FROM supplier_sku_mappings
            ) ranked
            WHERE rn = 1
        )
        """
    )
    op.drop_index("uq_supplier_sku_mapping_global_supplier_code")
    op.create_index(
        "uq_supplier_sku_mapping_global_supplier_code",
        "supplier_sku_mappings",
        ["supplier_name", "supplier_code"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
        sqlite_where=sa.text("organization_id IS NULL"),
    )
    with op.batch_alter_table("supplier_sku_mappings") as batch_op:
        batch_op.drop_constraint(
            "uq_supplier_sku_mapping_org_supplier_code_sku", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_supplier_sku_mapping_org_supplier_code",
            ["organization_id", "supplier_name", "supplier_code"],
        )
