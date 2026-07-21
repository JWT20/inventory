"""Reconcile uniquely matching orphaned inbound upload attempts.

Revision ID: 049
Revises: 048
Create Date: 2026-07-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Early clients knew the document hash but did not yet send the new
    # upload_attempt_id when creating a shipment. Repair only one-to-one matches
    # from the same organization and a narrow time window. Ambiguous rows remain
    # untouched for manual investigation.
    op.execute(
        sa.text(
            """
            WITH candidate_pairs AS (
                SELECT
                    a.id AS attempt_id,
                    s.id AS shipment_id,
                    s.status AS shipment_status,
                    s.created_at AS shipment_created_at,
                    s.booked_at,
                    COUNT(*) OVER (PARTITION BY a.id) AS shipments_for_attempt,
                    COUNT(*) OVER (PARTITION BY s.id) AS attempts_for_shipment
                FROM inbound_upload_attempts a
                JOIN inbound_shipments s
                  ON s.organization_id = a.organization_id
                 AND s.document_sha256 = a.document_sha256
                 AND s.created_at >= a.created_at
                 AND s.created_at <= a.created_at + INTERVAL '24 hours'
                WHERE a.shipment_id IS NULL
                  AND a.status = 'needs_action'
                  AND a.document_sha256 IS NOT NULL
                  AND s.status IN ('draft', 'booked')
            ),
            unique_pairs AS (
                SELECT *
                FROM candidate_pairs
                WHERE shipments_for_attempt = 1
                  AND attempts_for_shipment = 1
            ),
            line_stats AS (
                SELECT
                    shipment_id,
                    COUNT(*)::integer AS line_count,
                    COALESCE(SUM(quantity), 0)::integer AS total_quantity
                FROM inbound_shipment_lines
                GROUP BY shipment_id
            )
            UPDATE inbound_upload_attempts a
            SET
                shipment_id = p.shipment_id,
                status = p.shipment_status,
                bookable_line_count = COALESCE(ls.line_count, 0),
                booked_line_count = CASE
                    WHEN p.shipment_status = 'booked' THEN COALESCE(ls.line_count, 0)
                    ELSE 0
                END,
                booked_quantity = CASE
                    WHEN p.shipment_status = 'booked' THEN COALESCE(ls.total_quantity, 0)
                    ELSE 0
                END,
                error_stage = NULL,
                error_message = NULL,
                updated_at = COALESCE(p.booked_at, p.shipment_created_at, a.updated_at)
            FROM unique_pairs p
            LEFT JOIN line_stats ls ON ls.shipment_id = p.shipment_id
            WHERE a.id = p.attempt_id
            """
        )
    )


def downgrade() -> None:
    # The original missing links cannot be distinguished safely after repair.
    pass
