"""SKU attributes refactor: extract wine fields to flexible key-value table

Revision ID: 003
Revises: 002
Create Date: 2026-03-26

Moves wine-specific columns (producent, wijnaam, wijntype, jaargang, volume)
from the skus table into a generic sku_attributes key-value table.
Adds a category column to skus for future multi-product-type support.

The old wine columns are kept in the database (not dropped) as a safety net.
A future migration (phase C) will drop them once the refactor is validated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Wine fields to migrate from skus columns to sku_attributes rows
WINE_FIELDS = ("producent", "wijnaam", "wijntype", "jaargang", "volume")


def upgrade() -> None:
    # 1. Create sku_attributes table
    op.create_table(
        "sku_attributes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "sku_id",
            sa.Integer,
            sa.ForeignKey("skus.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.UniqueConstraint("sku_id", "key"),
    )
    op.create_index("ix_sku_attributes_sku_id", "sku_attributes", ["sku_id"])
    op.create_index("ix_sku_attributes_key", "sku_attributes", ["key"])

    # 2. Add category column to skus
    op.add_column(
        "skus",
        sa.Column("category", sa.String(50), nullable=True),
    )

    # 3. Migrate existing wine data into sku_attributes
    conn = op.get_bind()
    skus = conn.execute(
        sa.text("SELECT id, producent, wijnaam, wijntype, jaargang, volume FROM skus")
    ).fetchall()

    for sku in skus:
        sku_id = sku[0]
        values = {
            "producent": sku[1],
            "wijnaam": sku[2],
            "wijntype": sku[3],
            "jaargang": sku[4],
            "volume": sku[5],
        }
        for key, value in values.items():
            if value is not None and value.strip():
                conn.execute(
                    sa.text(
                        "INSERT INTO sku_attributes (sku_id, key, value) "
                        "VALUES (:sku_id, :key, :value)"
                    ),
                    {"sku_id": sku_id, "key": key, "value": value},
                )

    # 4. Set category = 'wine' on all existing SKUs
    conn.execute(sa.text("UPDATE skus SET category = 'wine' WHERE category IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_sku_attributes_key", table_name="sku_attributes")
    op.drop_index("ix_sku_attributes_sku_id", table_name="sku_attributes")
    op.drop_table("sku_attributes")
    op.drop_column("skus", "category")
