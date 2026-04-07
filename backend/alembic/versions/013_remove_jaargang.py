"""Remove jaargang (year) from SKU system

Revision ID: 013
Revises: 012
Create Date: 2026-04-07

Wines are not differentiated by vintage year, so jaargang is removed from
SKU codes, display names, and attributes. This migration:
1. Regenerates SKU codes (removing the year segment)
2. Regenerates display names (removing the year)
3. Deletes all jaargang rows from sku_attributes
4. Drops the legacy jaargang column from skus (left over from migration 003)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import unicodedata


# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _abbrev(s: str, length: int = 4) -> str:
    normalized = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    cleaned = ascii_only.strip().upper().replace(" ", "")
    return cleaned[:length]


def upgrade() -> None:
    conn = op.get_bind()

    # Fetch all wine SKUs that have a jaargang attribute
    rows = conn.execute(sa.text("""
        SELECT s.id, s.sku_code, s.name,
               MAX(CASE WHEN a.key = 'producent' THEN a.value END) AS producent,
               MAX(CASE WHEN a.key = 'wijnaam' THEN a.value END) AS wijnaam,
               MAX(CASE WHEN a.key = 'wijntype' THEN a.value END) AS wijntype,
               MAX(CASE WHEN a.key = 'volume' THEN a.value END) AS volume
        FROM skus s
        JOIN sku_attributes a ON a.sku_id = s.id
        WHERE s.category = 'wine'
        GROUP BY s.id, s.sku_code, s.name
    """)).fetchall()

    for row in rows:
        sku_id, old_code, old_name, producent, wijnaam, wijntype, volume = row
        if not all([producent, wijnaam, wijntype, volume]):
            continue

        new_code = "-".join([
            _abbrev(producent),
            _abbrev(wijnaam),
            _abbrev(wijntype, 3),
            volume.strip().replace("ml", "").replace("cl", ""),
        ])
        new_name = f"{producent} {wijnaam} {wijntype}"

        conn.execute(
            sa.text("UPDATE skus SET sku_code = :code, name = :name WHERE id = :id"),
            {"code": new_code, "name": new_name, "id": sku_id},
        )

    # Delete all jaargang attribute rows
    conn.execute(sa.text("DELETE FROM sku_attributes WHERE key = 'jaargang'"))

    # Drop legacy jaargang column if it still exists
    with op.batch_alter_table("skus") as batch_op:
        try:
            batch_op.drop_column("jaargang")
        except Exception:
            pass  # Column may already have been removed


def downgrade() -> None:
    # Re-add the legacy column
    op.add_column("skus", sa.Column("jaargang", sa.String(10), nullable=True))
