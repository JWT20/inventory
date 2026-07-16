"""Encrypt channel access tokens and remove the plaintext column.

Revision ID: 045
Revises: 044

PostgreSQL runs this DDL + data rewrite in one transaction. Every existing
token is encrypted and immediately decrypted for byte-for-byte verification
before the plaintext column is dropped. A missing/wrong key aborts everything.
"""
import base64
import binascii
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEY_ID = "v1"
_NONCE_BYTES = 12


def _decode_key() -> bytes:
    """Load the migration key without importing the application package.

    Alembic's console entry point does not guarantee that ``/app`` is on
    ``sys.path`` in the production image. Keeping the migration self-contained
    also makes its historical data format independent from future app changes.
    """
    encoded = os.environ.get("CHANNEL_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not encoded:
        raise RuntimeError("CHANNEL_CREDENTIAL_ENCRYPTION_KEY ontbreekt")
    try:
        padding = "=" * (-len(encoded) % 4)
        key = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("CHANNEL_CREDENTIAL_ENCRYPTION_KEY is ongeldig") from exc
    if len(key) != 32:
        raise RuntimeError(
            "CHANNEL_CREDENTIAL_ENCRYPTION_KEY moet exact 32 bytes zijn"
        )
    return key


def _aad(connection_id: int, organization_id: int, channel: str) -> bytes:
    return (
        f"channel-credential:{_KEY_ID}:connection:{connection_id}:"
        f"organization:{organization_id}:channel:{channel}:access-token"
    ).encode("utf-8")


def _encrypt_token(
    token: str, *, connection_id: int, organization_id: int, channel: str
) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = AESGCM(_decode_key()).encrypt(
        nonce,
        token.encode("utf-8"),
        _aad(connection_id, organization_id, channel),
    )
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def _decrypt_token(
    ciphertext: str, *, connection_id: int, organization_id: int, channel: str
) -> str:
    try:
        packed = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        if len(packed) <= _NONCE_BYTES:
            raise ValueError("ciphertext te kort")
        nonce, encrypted = packed[:_NONCE_BYTES], packed[_NONCE_BYTES:]
        return AESGCM(_decode_key()).decrypt(
            nonce,
            encrypted,
            _aad(connection_id, organization_id, channel),
        ).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise RuntimeError(
            "Kanaalcredential kan niet veilig worden ontsleuteld"
        ) from exc


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
        ciphertext = _encrypt_token(
            row["access_token"],
            connection_id=row["id"],
            organization_id=row["organization_id"],
            channel=row["channel"],
        )
        verified = _decrypt_token(
            ciphertext,
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
                "key_id": _KEY_ID,
                "connection_id": row["id"],
            },
        )

    op.drop_column("channel_connections", "access_token")


def downgrade() -> None:
    raise RuntimeError(
        "Migratie 045 is bewust onomkeerbaar: plaintext access tokens worden niet hersteld"
    )
