"""Add delivery_day to order_lines table

Revision ID: 018
Revises: 017
Create Date: 2026-04-09

Each order line now stores the chosen delivery day (wednesday/thursday/friday).
Defaults to thursday. Allows customers to override their default per order.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_lines",
        sa.Column(
            "delivery_day",
            sa.String(20),
            nullable=False,
            server_default="thursday",
        ),
    )
    # Backfill existing lines from their customer's delivery_day
    op.execute("""
        UPDATE order_lines
        SET delivery_day = customers.delivery_day
        FROM customers
        WHERE order_lines.customer_id = customers.id
    """)


def downgrade() -> None:
    op.drop_column("order_lines", "delivery_day")
