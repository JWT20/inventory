"""Courier rate cards + settlements

Revision ID: 028
Revises: 027
Create Date: 2026-06-04

Replaces the single-row courier_billing_rates config with a scalable tariff
layer:
- courier_rate_cards: per courier/customer/org/service tariffs, effective-dated
- courier_settlements + courier_settlement_lines: frozen monthly billing runs

The existing global rate is migrated into a catch-all rate card, then the old
table is dropped. Any SKU category outside the locked-down set is backfilled to
'other' so billing never falls through to the wrong tariff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Lock down SKU categories: backfill anything unexpected to 'other' ---
    op.execute(
        """
        UPDATE skus
        SET category = 'other'
        WHERE category IS NULL OR category NOT IN ('wine', 'book', 'other')
        """
    )

    # --- Rate cards ---
    op.create_table(
        "courier_rate_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("courier_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("customer_id", sa.Integer(),
                  sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("service_type", sa.String(length=30), nullable=True),
        sa.Column("unit_type", sa.String(length=20), nullable=False, server_default="box"),
        sa.Column("charge_cents", sa.Integer(), nullable=False),
        sa.Column("platform_cents", sa.Integer(), nullable=False),
        sa.Column("courier_cents", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    # Postgres 15+: NULLs collide so duplicate all-NULL "default" cards are
    # rejected. (Ignored by SQLite; app-level overlap guard covers both.)
    op.create_index(
        "uq_courier_rate_cards_scope",
        "courier_rate_cards",
        ["courier_id", "customer_id", "organization_id", "service_type", "effective_from"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # --- Settlements ---
    op.create_table(
        "courier_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("courier_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("total_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_charge_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_platform_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_courier_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("courier_id", "period_month", name="uq_settlement_courier_month"),
    )
    op.create_table(
        "courier_settlement_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("settlement_id", sa.Integer(),
                  sa.ForeignKey("courier_settlements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(),
                  sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("service_type", sa.String(length=30), nullable=False),
        sa.Column("unit_type", sa.String(length=20), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("rate_card_id", sa.Integer(),
                  sa.ForeignKey("courier_rate_cards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("charge_cents", sa.Integer(), nullable=False),
        sa.Column("platform_cents", sa.Integer(), nullable=False),
        sa.Column("courier_cents", sa.Integer(), nullable=False),
        sa.Column("line_charge_cents", sa.Integer(), nullable=False),
        sa.Column("line_platform_cents", sa.Integer(), nullable=False),
        sa.Column("line_courier_cents", sa.Integer(), nullable=False),
    )

    # --- Migrate the old single global rate into a catch-all rate card ---
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT charge_cents, platform_cents, courier_cents "
            "FROM courier_billing_rates WHERE id = 1"
        )
    ).first()
    charge, platform, courier = (row.charge_cents, row.platform_cents, row.courier_cents) if row else (50, 17, 33)
    bind.execute(
        sa.text(
            """
            INSERT INTO courier_rate_cards
                (courier_id, customer_id, organization_id, service_type, unit_type,
                 charge_cents, platform_cents, courier_cents, effective_from)
            VALUES
                (NULL, NULL, NULL, NULL, 'box',
                 :charge, :platform, :courier, :eff_from)
            """
        ),
        {"charge": charge, "platform": platform, "courier": courier,
         "eff_from": "2000-01-01"},
    )

    op.drop_table("courier_billing_rates")


def downgrade() -> None:
    op.create_table(
        "courier_billing_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("charge_cents", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("platform_cents", sa.Integer(), nullable=False, server_default="17"),
        sa.Column("courier_cents", sa.Integer(), nullable=False, server_default="33"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO courier_billing_rates (id, charge_cents, platform_cents, courier_cents) "
        "VALUES (1, 50, 17, 33)"
    )
    op.drop_table("courier_settlement_lines")
    op.drop_table("courier_settlements")
    op.drop_index("uq_courier_rate_cards_scope", table_name="courier_rate_cards")
    op.drop_table("courier_rate_cards")
