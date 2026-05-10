"""Prevent duplicate inbound pakbonnen

Revision ID: 024
Revises: 023
Create Date: 2026-05-10

- Renames duplicate inbound_shipments by suffixing reference with -dup-N,
  keeping the oldest row's reference intact.
- Adds a partial unique index on (organization_id, supplier_name, reference)
  so future duplicates are blocked at the database level.
- Adds document_sha256 column + index for file-fingerprint based duplicate
  detection during upload.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Step 0: rename duplicates by appending -dup-N to all but the oldest
    # in each (organization_id, supplier_name, reference) group.
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    reference,
                    ROW_NUMBER() OVER (
                        PARTITION BY organization_id, supplier_name, reference
                        ORDER BY created_at, id
                    ) AS rn
                FROM inbound_shipments
                WHERE reference IS NOT NULL
                  AND reference <> ''
                  AND status <> 'cancelled'
            )
            UPDATE inbound_shipments AS s
            SET reference = ranked.reference || '-dup-' || ranked.rn
            FROM ranked
            WHERE s.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )

    # Step 1: partial unique index on the natural key.
    op.create_index(
        "ux_inbound_shipments_org_supplier_ref",
        "inbound_shipments",
        ["organization_id", "supplier_name", "reference"],
        unique=True,
        postgresql_where=sa.text(
            "reference IS NOT NULL AND reference <> '' AND status <> 'cancelled'"
        ),
    )

    # Step 2: document fingerprint column + index.
    op.add_column(
        "inbound_shipments",
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_inbound_shipments_org_sha",
        "inbound_shipments",
        ["organization_id", "document_sha256"],
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_shipments_org_sha", table_name="inbound_shipments")
    op.drop_column("inbound_shipments", "document_sha256")
    op.drop_index(
        "ux_inbound_shipments_org_supplier_ref",
        table_name="inbound_shipments",
    )
    # Renamed references are not reverted; the -dup-N suffixes remain.
