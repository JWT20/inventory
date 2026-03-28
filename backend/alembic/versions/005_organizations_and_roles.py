"""Add organizations table, refactor user roles and data ownership.

- Create organizations table (merchants only)
- Add organization_id FK to users (nullable)
- Add is_platform_admin to users
- Rename merchant_id -> organization_id on orders, inbound_shipments,
  inventory_balances, stock_movements
- Add organization_id to customers, skus
- Migrate existing merchant users to organizations
- Update role values: admin->owner (org-bound), courier stays, new customer role

Revision ID: 005
Revises: 004
Create Date: 2026-03-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "enabled_modules",
            sa.Text,
            nullable=False,
            server_default='["inventory","orders"]',
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 2. Add new columns to users
    op.add_column(
        "users",
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )

    # 3. Migrate existing admin users -> is_platform_admin=true
    op.execute(
        "UPDATE users SET is_platform_admin = true WHERE role = 'admin'"
    )

    # 4. Add organization_id to data tables (nullable initially for migration)
    op.add_column("orders", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("orders", sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True))
    op.add_column("inbound_shipments", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("inventory_balances", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("stock_movements", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("customers", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("skus", sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True))

    # 5. For each existing merchant user, create an organization and link data
    conn = op.get_bind()
    merchants = conn.execute(
        sa.text("SELECT id, username FROM users WHERE role = 'merchant'")
    ).fetchall()

    for merchant_id, username in merchants:
        slug = username.lower().replace(" ", "-")
        conn.execute(
            sa.text(
                "INSERT INTO organizations (name, slug) VALUES (:name, :slug)"
            ),
            {"name": username, "slug": slug},
        )
        org_id = conn.execute(
            sa.text("SELECT id FROM organizations WHERE slug = :slug"),
            {"slug": slug},
        ).scalar()

        # Link merchant user to org as owner
        conn.execute(
            sa.text("UPDATE users SET organization_id = :org_id, role = 'owner' WHERE id = :uid"),
            {"org_id": org_id, "uid": merchant_id},
        )

        # Migrate data ownership
        conn.execute(
            sa.text("UPDATE orders SET organization_id = :org_id, created_by = :uid WHERE merchant_id = :uid"),
            {"org_id": org_id, "uid": merchant_id},
        )
        conn.execute(
            sa.text("UPDATE inbound_shipments SET organization_id = :org_id WHERE merchant_id = :uid"),
            {"org_id": org_id, "uid": merchant_id},
        )
        conn.execute(
            sa.text("UPDATE inventory_balances SET organization_id = :org_id WHERE merchant_id = :uid"),
            {"org_id": org_id, "uid": merchant_id},
        )
        conn.execute(
            sa.text("UPDATE stock_movements SET organization_id = :org_id WHERE merchant_id = :uid"),
            {"org_id": org_id, "uid": merchant_id},
        )

    # 6. Drop old merchant_id columns (keep as nullable for safety, remove FKs)
    # We'll drop the old columns
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("orders_merchant_id_fkey", type_="foreignkey")
        batch_op.drop_column("merchant_id")

    with op.batch_alter_table("inbound_shipments") as batch_op:
        batch_op.drop_constraint("inbound_shipments_merchant_id_fkey", type_="foreignkey")
        batch_op.drop_column("merchant_id")

    with op.batch_alter_table("inventory_balances") as batch_op:
        batch_op.drop_constraint("inventory_balances_merchant_id_fkey", type_="foreignkey")
        # Drop old unique constraint before dropping column
        batch_op.drop_constraint("inventory_balances_sku_id_merchant_id_key", type_="unique")
        batch_op.drop_column("merchant_id")
        # Add new unique constraint
        batch_op.create_unique_constraint(
            "inventory_balances_sku_id_organization_id_key",
            ["sku_id", "organization_id"],
        )

    with op.batch_alter_table("stock_movements") as batch_op:
        batch_op.drop_constraint("stock_movements_merchant_id_fkey", type_="foreignkey")
        batch_op.drop_column("merchant_id")

    # 7. Remove uniqueness on customers.name (now org-scoped)
    # The unique constraint was created inline with the column in 001.
    # PostgreSQL names it "customers_name_key" by default.
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_constraint("customers_name_key", type_="unique")


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for this migration")
