"""Add courier_billing_rates config table

Revision ID: 027
Revises: 026
Create Date: 2026-06-04

Stores the configurable per-box billing rate couriers charge customers
(default EUR 0.50) and how it splits between the platform owner
(EUR 0.17) and the courier (EUR 0.33). A single seed row (id=1) holds
the active rate so it can be tuned in the database without a code change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "courier_billing_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("charge_cents", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("platform_cents", sa.Integer(), nullable=False, server_default="17"),
        sa.Column("courier_cents", sa.Integer(), nullable=False, server_default="33"),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        INSERT INTO courier_billing_rates (id, charge_cents, platform_cents, courier_cents)
        VALUES (1, 50, 17, 33)
        """
    )


def downgrade() -> None:
    op.drop_table("courier_billing_rates")
