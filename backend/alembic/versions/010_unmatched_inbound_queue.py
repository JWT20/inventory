"""Add unmatched inbound queue table.

Revision ID: 010
Revises: 009
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unmatched_inbound_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("document_type", sa.String(length=20), nullable=True),
        sa.Column("image_key", sa.String(length=500), nullable=True),
        sa.Column("supplier_code", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity_boxes", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("bbox_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("resolved_sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_unmatched_inbound_lines_org_status", "unmatched_inbound_lines", ["organization_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_unmatched_inbound_lines_org_status", table_name="unmatched_inbound_lines")
    op.drop_table("unmatched_inbound_lines")
