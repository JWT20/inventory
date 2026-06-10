"""Add CHECK constraints for status/role/type enum columns.

The allowed values previously existed only as Python tuples in app.models;
nothing stopped a bug or manual SQL from writing a retired or misspelled
value. These constraints make the database reject anything outside the
allowed lists.

Note: Postgres validates existing rows when the constraint is added, so this
migration fails loudly if legacy data contains an out-of-range value — that
is intentional.

Revision ID: 031
Revises: 030
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors the VALID_* tuples in app.models at the time of this revision.
CONSTRAINTS = [
    (
        "users",
        "ck_users_role",
        "role IN ('owner', 'member', 'courier', 'customer')",
    ),
    (
        "orders",
        "ck_orders_status",
        "status IN ('pending_approval', 'pending_images', 'active', "
        "'completed', 'cancelled', 'closed')",
    ),
    (
        "inbound_shipments",
        "ck_inbound_shipments_status",
        "status IN ('draft', 'booked', 'cancelled')",
    ),
    (
        "stock_movements",
        "ck_stock_movements_movement_type",
        "movement_type IN ('receive', 'pick', 'adjust', 'count')",
    ),
    (
        "customer_skus",
        "ck_customer_skus_discount_type",
        "discount_type IS NULL OR discount_type IN ('percentage', 'fixed')",
    ),
]


def upgrade() -> None:
    for table, name, condition in CONSTRAINTS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    for table, name, _condition in CONSTRAINTS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="check")
