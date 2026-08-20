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
    # An *active* hold that is split across two shelves cannot be narrowed here.
    # Collapsing the lines is only half of it: the bottles are also reserved on
    # two ``inventory_balances`` rows, and after the column is gone the release
    # settles everything against the reservation's own pool. The other pool
    # would keep its reservation forever, and a collect would book stock off a
    # shelf the bottles never stood on.
    #
    # Moving those reservations around inside a rollback would be quietly
    # rewriting live stock numbers, so this refuses instead and says what to do:
    # a split hold is settled in seconds by collecting or releasing it. Holds
    # that are already collected or released hold nothing and pass fine.
    split = (
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT l.reservation_id, l.sku_id
                FROM advice_reservation_lines AS l
                JOIN advice_reservations AS r ON r.id = l.reservation_id
                WHERE r.status = 'active'
                GROUP BY l.reservation_id, l.sku_id
                HAVING COUNT(DISTINCT l.inventory_location) > 1
            ) AS split_holds
            """
        )
    )
    blocking = op.get_bind().execute(split).scalar() or 0
    if blocking:
        raise RuntimeError(
            f"{blocking} actieve reservering(en) staan over winkel én webshop "
            "verdeeld. Haal die eerst op of geef ze vrij; daarna kan deze "
            "migratie teruggedraaid worden."
        )

    # Narrowing back to one row per product has to collapse a split hold. Sum
    # the quantities onto the surviving row first: a hold of three from the
    # webshop and one from the shop is four bottles, and dropping the second row
    # before adding it up would release one of them into thin air.
    op.execute(
        """
        UPDATE advice_reservation_lines
        SET quantity = (
            SELECT SUM(sibling.quantity)
            FROM advice_reservation_lines AS sibling
            WHERE sibling.reservation_id = advice_reservation_lines.reservation_id
              AND sibling.sku_id = advice_reservation_lines.sku_id
        )
        WHERE id IN (
            SELECT MIN(id) FROM advice_reservation_lines GROUP BY reservation_id, sku_id
        )
        """
    )
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
