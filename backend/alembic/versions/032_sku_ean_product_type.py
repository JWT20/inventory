"""Add skus.product_type and skus.ean (barcode product identity).

product_type tells how a product is identified: "barcode" (matched on its
EAN/GTIN scan) or "vision" (matched via reference photos + AI, the wine flow).
New products default to "barcode"; every existing product is wine, so they are
backfilled to "vision" to keep their photo-based matching.

ean holds the EAN-13 barcode, unique per organization (NULL for vision
products, which are excluded from the unique index).

Revision ID: 032
Revises: 031
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills every existing row as "barcode"; we immediately
    # flip them to "vision" because the whole current catalogue is wine.
    op.add_column(
        "skus",
        sa.Column(
            "product_type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'barcode'"),
        ),
    )
    op.execute("UPDATE skus SET product_type = 'vision'")

    op.add_column("skus", sa.Column("ean", sa.String(length=13), nullable=True))

    # Unique per organization, NULL eans excluded so all vision products coexist.
    op.create_index(
        "uq_skus_org_ean",
        "skus",
        ["organization_id", "ean"],
        unique=True,
        postgresql_where=sa.text("ean IS NOT NULL"),
        sqlite_where=sa.text("ean IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_skus_org_ean", table_name="skus")
    op.drop_column("skus", "ean")
    op.drop_column("skus", "product_type")
