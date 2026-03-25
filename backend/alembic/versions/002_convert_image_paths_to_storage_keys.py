"""Convert absolute image_path values to relative storage keys.

Revision ID: 002
Revises: 001
Create Date: 2026-03-25

Strips the /app/uploads/ prefix from image_path and scan_image_path columns
so they become relative keys compatible with the storage abstraction layer.

Examples:
    /app/uploads/reference_images/5/abc.jpg  →  reference_images/5/abc.jpg
    /app/uploads/scans/def.jpg               →  scans/def.jpg
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Strip /app/uploads/ prefix from reference_images.image_path
    op.execute("""
        UPDATE reference_images
        SET image_path = REGEXP_REPLACE(image_path, '^.*/uploads/', '')
        WHERE image_path LIKE '%/uploads/%'
    """)

    # Strip /app/uploads/ prefix from bookings.scan_image_path
    op.execute("""
        UPDATE bookings
        SET scan_image_path = REGEXP_REPLACE(scan_image_path, '^.*/uploads/', '')
        WHERE scan_image_path LIKE '%/uploads/%'
    """)


def downgrade() -> None:
    # Re-add /app/uploads/ prefix
    op.execute("""
        UPDATE reference_images
        SET image_path = '/app/uploads/' || image_path
        WHERE image_path NOT LIKE '/%'
    """)

    op.execute("""
        UPDATE bookings
        SET scan_image_path = '/app/uploads/' || scan_image_path
        WHERE scan_image_path IS NOT NULL
          AND scan_image_path NOT LIKE '/%'
    """)
