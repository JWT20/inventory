"""Backfill organization enabled_modules for module-based scoping.

Module gating goes live in this release: ``enabled_modules`` now decides which
flows exist and which endpoints a merchant may reach. Every existing org is set
to the barcode-fulfilment baseline; the wine merchant (slug 'jurjen')
additionally gets the vision/portal/week/suppliers modules so its existing flows
keep working. Run together with the gating code — deployed apart, merchants
would lose tabs they should keep.

If production has other wine-style orgs besides 'jurjen', re-enable their
modules via the admin org-editor after deploy.

Revision ID: 033
Revises: 032
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BASELINE = '["inventory","orders","barcode_picking","channel_orders"]'
JURJEN = '["inventory","orders","vision_picking","customer_portal","week_overview","suppliers"]'


def upgrade() -> None:
    # Everyone gets the barcode-fulfilment baseline first.
    op.execute(f"UPDATE organizations SET enabled_modules = '{BASELINE}'")
    # The wine merchant keeps its vision/portal/week/suppliers world.
    op.execute(
        f"UPDATE organizations SET enabled_modules = '{JURJEN}' WHERE slug = 'jurjen'"
    )


def downgrade() -> None:
    # Restore the pre-gating default so nothing depends on the new module names.
    op.execute("""UPDATE organizations SET enabled_modules = '["inventory","orders"]'""")
