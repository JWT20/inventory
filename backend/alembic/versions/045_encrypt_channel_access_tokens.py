"""Encrypt channel access tokens and remove the plaintext column.

Revision ID: 045
Revises: 044

PostgreSQL runs this DDL + data rewrite in one transaction. Every existing
token is encrypted and immediately decrypted for byte-for-byte verification
before the plaintext column is dropped. A missing/wrong key aborts everything.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.channel_credentials import (
    decrypt_access_token_value,
    encrypt_access_token_value,
)


revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channel_connections",
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "channel_connections",
        sa.Column("access_token_key_id", sa.String(length=32), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, organization_id, channel, access_token
            FROM channel_connections
            WHERE access_token IS NOT NULL AND access_token <> ''
            FOR UPDATE
            """
        )
    ).mappings()
    for row in rows:
        ciphertext, key_id = encrypt_access_token_value(
            row["access_token"],
            connection_id=row["id"],
            organization_id=row["organization_id"],
            channel=row["channel"],
        )
        verified = decrypt_access_token_value(
            ciphertext,
            key_id=key_id,
            connection_id=row["id"],
            organization_id=row["organization_id"],
            channel=row["channel"],
        )
        if verified != row["access_token"]:
            raise RuntimeError(
                f"Credential encryptieverificatie faalde voor connection {row['id']}"
            )
        bind.execute(
            sa.text(
                """
                UPDATE channel_connections
                SET access_token_encrypted = :ciphertext,
                    access_token_key_id = :key_id
                WHERE id = :connection_id
                """
            ),
            {
                "ciphertext": ciphertext,
                "key_id": key_id,
                "connection_id": row["id"],
            },
        )

    op.drop_column("channel_connections", "access_token")


def downgrade() -> None:
    raise RuntimeError(
        "Migratie 045 is bewust onomkeerbaar: plaintext access tokens worden niet hersteld"
    )
