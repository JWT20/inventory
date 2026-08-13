"""Split stock into warehouse/store and add pickup reservations.

Revision ID: 054
Revises: 053
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_balances",
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="warehouse",
        ),
    )
    with op.batch_alter_table("inventory_balances") as batch_op:
        batch_op.drop_constraint(
            "inventory_balances_sku_id_organization_id_key", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_inventory_balances_sku_org_location",
            ["sku_id", "organization_id", "inventory_location"],
        )

    op.add_column(
        "stock_movements",
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="warehouse",
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="warehouse",
        ),
    )
    op.add_column(
        "inbound_shipments",
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="warehouse",
        ),
    )
    op.add_column(
        "advice_sales",
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="store",
        ),
    )

    # Bottles live on the shop shelf, not in the warehouse: the advice app sells
    # them at the counter and in the webshop. Without this the store feed reads
    # zero for every wine on the first request after deploy. Balances move
    # wholesale; the movement log is left untouched so the audit trail keeps
    # saying where the stock actually came from.
    op.execute(
        """
        UPDATE inventory_balances
        SET inventory_location = 'store'
        WHERE inventory_location = 'warehouse'
          AND sku_id IN (SELECT id FROM skus WHERE is_bottle IS TRUE)
        """
    )

    op.create_table(
        "advice_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("external_order_id", sa.String(length=100), nullable=False),
        sa.Column("order_reference", sa.String(length=100), nullable=True),
        sa.Column(
            "fulfillment_method",
            sa.String(length=20),
            nullable=False,
            server_default="pickup",
        ),
        sa.Column(
            "inventory_location",
            sa.String(length=20),
            nullable=False,
            server_default="store",
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "external_order_id",
            name="uq_advice_reservations_org_external_order",
        ),
    )
    op.create_table(
        "advice_reservation_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["advice_reservations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reservation_id",
            "sku_id",
            name="uq_advice_reservation_lines_reservation_sku",
        ),
    )


def downgrade() -> None:
    op.drop_table("advice_reservation_lines")
    op.drop_table("advice_reservations")
    op.drop_column("advice_sales", "inventory_location")
    op.drop_column("inbound_shipments", "inventory_location")
    op.drop_column("orders", "inventory_location")
    op.drop_column("stock_movements", "inventory_location")
    # Collapsing the two pools back into one would violate the restored
    # (sku_id, organization_id) uniqueness, so fold every extra location into
    # the lowest-id row for that SKU first.
    op.execute(
        """
        UPDATE inventory_balances AS keep
        SET quantity_on_hand = totals.on_hand,
            quantity_reserved = totals.reserved
        FROM (
            SELECT sku_id,
                   organization_id,
                   MIN(id) AS keep_id,
                   SUM(quantity_on_hand) AS on_hand,
                   SUM(quantity_reserved) AS reserved
            FROM inventory_balances
            GROUP BY sku_id, organization_id
        ) AS totals
        WHERE keep.id = totals.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM inventory_balances
        WHERE id NOT IN (
            SELECT MIN(id) FROM inventory_balances
            GROUP BY sku_id, organization_id
        )
        """
    )
    with op.batch_alter_table("inventory_balances") as batch_op:
        batch_op.drop_constraint(
            "uq_inventory_balances_sku_org_location", type_="unique"
        )
        batch_op.create_unique_constraint(
            "inventory_balances_sku_id_organization_id_key",
            ["sku_id", "organization_id"],
        )
    op.drop_column("inventory_balances", "inventory_location")
