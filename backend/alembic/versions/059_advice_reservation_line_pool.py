"""Record which pool each advice reservation line holds its bottles in.

The shop and the webshop are two physical places but one sellable pool, so an
order for four bottles may legitimately be covered by three from the webshop
shelf and one from the shop. A reservation therefore cannot name a single pool
for everything it holds: collecting or releasing has to give each bottle back
where it was taken from, or the counts drift apart.

The line gains the pool, and the per-line uniqueness widens to include it so one
product may be held in both places at once. Existing lines inherit the pool
their reservation was taken from, which is exactly what they held.

Revision ID: 059
Revises: 058
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "advice_reservation_lines",
        sa.Column("inventory_location", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE advice_reservation_lines
        SET inventory_location = (
            SELECT inventory_location
            FROM advice_reservations
            WHERE advice_reservations.id = advice_reservation_lines.reservation_id
        )
        """
    )
    with op.batch_alter_table("advice_reservation_lines") as batch_op:
        batch_op.alter_column(
            "inventory_location",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default="store",
        )
        batch_op.drop_constraint(
            "uq_advice_reservation_lines_reservation_sku", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_advice_reservation_lines_reservation_sku_location",
            ["reservation_id", "sku_id", "inventory_location"],
        )


def downgrade() -> None:
    # Narrowing back to one row per product has to collapse a split hold; the
    # quantities are summed onto the reservation's own pool so nothing is lost.
    op.execute(
        """
        DELETE FROM advice_reservation_lines
        WHERE id NOT IN (
            SELECT MIN(id) FROM advice_reservation_lines GROUP BY reservation_id, sku_id
        )
        """
    )
    with op.batch_alter_table("advice_reservation_lines") as batch_op:
        batch_op.drop_constraint(
            "uq_advice_reservation_lines_reservation_sku_location", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_advice_reservation_lines_reservation_sku",
            ["reservation_id", "sku_id"],
        )
        batch_op.drop_column("inventory_location")
