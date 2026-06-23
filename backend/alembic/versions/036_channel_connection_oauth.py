"""Add OAuth token columns to channel_connections (Fase 3 Shopify OAuth).

The Shopify app is an OAuth app (Client ID + secret, no static token), so the
access token is obtained via the install flow and stored per connection.

Revision ID: 036
Revises: 035
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("channel_connections", sa.Column("shop_domain", sa.String(length=255), nullable=True))
    op.add_column("channel_connections", sa.Column("access_token", sa.Text(), nullable=True))
    op.add_column("channel_connections", sa.Column("scope", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_connections", "scope")
    op.drop_column("channel_connections", "access_token")
    op.drop_column("channel_connections", "shop_domain")
