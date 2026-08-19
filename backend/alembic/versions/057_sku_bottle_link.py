"""Link a box product to the bottle product it contains.

A merchant orders replenishment per box, but the shop and the webshop sell
loose bottles. Without a link between the two catalog entries nothing can turn
one picked box into six bottles on the shelf, so the pool that is actually sold
from can never be filled by a pick.

The link lives on the box (``skus.bottle_sku_id`` → the bottle SKU). Nullable:
every existing product keeps working unlinked, and this migration therefore
changes no behaviour on its own.

Revision ID: 057
Revises: 056
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so SQLite (used by the test suite) can add the
    # self-referencing foreign key too.
    with op.batch_alter_table("skus") as batch_op:
        batch_op.add_column(sa.Column("bottle_sku_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_skus_bottle_sku_id",
            "skus",
            ["bottle_sku_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_skus_bottle_sku_id", "skus", ["bottle_sku_id"])


def downgrade() -> None:
    op.drop_index("ix_skus_bottle_sku_id", table_name="skus")
    with op.batch_alter_table("skus") as batch_op:
        batch_op.drop_constraint("fk_skus_bottle_sku_id", type_="foreignkey")
        batch_op.drop_column("bottle_sku_id")
