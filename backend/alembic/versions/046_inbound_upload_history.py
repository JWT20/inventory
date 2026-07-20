"""Add durable inbound upload history.

Revision ID: 046
Revises: 045
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbound_upload_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "shipment_id",
            sa.Integer(),
            sa.ForeignKey("inbound_shipments.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="file"),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("document_key", sa.String(length=500), nullable=True),
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="processing"),
        sa.Column("error_stage", sa.String(length=30), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bookable_line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("booked_line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("booked_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_inbound_uploads_org_created",
        "inbound_upload_attempts",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_inbound_uploads_org_sha",
        "inbound_upload_attempts",
        ["organization_id", "document_sha256"],
    )

    # Existing shipments remain useful history, even though their original
    # uploader, filename and preview state were never stored.
    op.execute(
        sa.text(
            """
            INSERT INTO inbound_upload_attempts (
                organization_id,
                shipment_id,
                source_type,
                document_sha256,
                supplier_name,
                reference,
                status,
                line_count,
                bookable_line_count,
                booked_line_count,
                booked_quantity,
                created_at,
                updated_at
            )
            SELECT
                s.organization_id,
                s.id,
                'legacy',
                s.document_sha256,
                s.supplier_name,
                s.reference,
                s.status,
                COUNT(l.id)::integer,
                COUNT(l.id)::integer,
                CASE WHEN s.status = 'booked' THEN COUNT(l.id)::integer ELSE 0 END,
                CASE WHEN s.status = 'booked' THEN COALESCE(SUM(l.quantity), 0)::integer ELSE 0 END,
                s.created_at,
                COALESCE(s.booked_at, s.created_at)
            FROM inbound_shipments s
            LEFT JOIN inbound_shipment_lines l ON l.shipment_id = s.id
            GROUP BY s.id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_uploads_org_sha", table_name="inbound_upload_attempts")
    op.drop_index("ix_inbound_uploads_org_created", table_name="inbound_upload_attempts")
    op.drop_table("inbound_upload_attempts")
