"""Authenticated encryption for per-organization integration credentials.

Sales channels (Shopify, bol) and shipping carriers (Veloyd) both store a
secret per organization, and both use the key supplied by the runtime
environment, deliberately kept outside PostgreSQL. Ciphertext is bound to one
connection through AES-GCM additional authenticated data, so copying it to
another organization, channel or carrier cannot turn it into a usable
credential — a channel token pasted into a carrier row fails to decrypt.
"""
from __future__ import annotations

import base64
import binascii
import os
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

if TYPE_CHECKING:
    from app.models import CarrierConnection, ChannelConnection


KEY_ID = "v1"
_NONCE_BYTES = 12


class CredentialEncryptionError(RuntimeError):
    """A credential cannot be encrypted/decrypted safely."""


def _decode_key(raw_key: str | None = None) -> bytes:
    encoded = (
        raw_key
        if raw_key is not None
        else settings.channel_credential_encryption_key
    ).strip()
    if not encoded:
        raise CredentialEncryptionError(
            "CHANNEL_CREDENTIAL_ENCRYPTION_KEY ontbreekt"
        )
    try:
        padding = "=" * (-len(encoded) % 4)
        key = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, binascii.Error) as exc:
        raise CredentialEncryptionError(
            "CHANNEL_CREDENTIAL_ENCRYPTION_KEY is ongeldig"
        ) from exc
    if len(key) != 32:
        raise CredentialEncryptionError(
            "CHANNEL_CREDENTIAL_ENCRYPTION_KEY moet exact 32 bytes zijn"
        )
    return key


def _aad(connection_id: int, organization_id: int, channel: str, key_id: str) -> bytes:
    return (
        f"channel-credential:{key_id}:connection:{connection_id}:"
        f"organization:{organization_id}:channel:{channel}:access-token"
    ).encode("utf-8")


def _encrypt(value: str, aad: bytes, *, raw_key: str | None = None) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = AESGCM(_decode_key(raw_key)).encrypt(nonce, value.encode("utf-8"), aad)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def _decrypt(
    ciphertext: str,
    aad: bytes,
    *,
    subject: str = "Kanaalcredential",
    raw_key: str | None = None,
) -> str:
    """Decrypt one secret, naming in the error which kind failed."""
    try:
        packed = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        if len(packed) <= _NONCE_BYTES:
            raise ValueError("ciphertext te kort")
        nonce, encrypted = packed[:_NONCE_BYTES], packed[_NONCE_BYTES:]
        plaintext = AESGCM(_decode_key(raw_key)).decrypt(nonce, encrypted, aad)
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise CredentialEncryptionError(
            f"{subject} kan niet veilig worden ontsleuteld"
        ) from exc


def encrypt_access_token_value(
    token: str,
    *,
    connection_id: int,
    organization_id: int,
    channel: str,
    raw_key: str | None = None,
) -> tuple[str, str]:
    """Return ``(base64url ciphertext, key_id)`` without logging the token."""
    if not token:
        raise CredentialEncryptionError("Leeg access token kan niet worden opgeslagen")
    ciphertext = _encrypt(
        token,
        _aad(connection_id, organization_id, channel, KEY_ID),
        raw_key=raw_key,
    )
    return ciphertext, KEY_ID


def decrypt_access_token_value(
    ciphertext: str,
    *,
    key_id: str,
    connection_id: int,
    organization_id: int,
    channel: str,
    raw_key: str | None = None,
) -> str:
    """Decrypt one token, failing closed on malformed/swapped/tampered data."""
    if key_id != KEY_ID:
        raise CredentialEncryptionError(f"Onbekende credential key-id: {key_id}")
    return _decrypt(
        ciphertext,
        _aad(connection_id, organization_id, channel, key_id),
        raw_key=raw_key,
    )


def store_access_token(connection: "ChannelConnection", token: str) -> None:
    if connection.id is None:
        raise CredentialEncryptionError(
            "ChannelConnection moet zijn opgeslagen voordat het token wordt versleuteld"
        )
    ciphertext, key_id = encrypt_access_token_value(
        token,
        connection_id=connection.id,
        organization_id=connection.organization_id,
        channel=connection.channel,
    )
    connection.access_token_encrypted = ciphertext
    connection.access_token_key_id = key_id


def get_access_token(connection: "ChannelConnection") -> str | None:
    if not connection.access_token_encrypted:
        return None
    if not connection.access_token_key_id:
        raise CredentialEncryptionError("Kanaalcredential mist een key-id")
    return decrypt_access_token_value(
        connection.access_token_encrypted,
        key_id=connection.access_token_key_id,
        connection_id=connection.id,
        organization_id=connection.organization_id,
        channel=connection.channel,
    )


def has_access_token(connection: "ChannelConnection | None") -> bool:
    return bool(
        connection
        and connection.access_token_encrypted
        and connection.access_token_key_id
    )


def _carrier_aad(
    connection_id: int, organization_id: int, carrier: str, key_id: str
) -> bytes:
    """A namespace of its own, so a channel token cannot be reused as a key."""
    return (
        f"carrier-credential:{key_id}:connection:{connection_id}:"
        f"organization:{organization_id}:carrier:{carrier}:api-key"
    ).encode("utf-8")


def store_carrier_api_key(connection: "CarrierConnection", api_key: str) -> None:
    if connection.id is None:
        raise CredentialEncryptionError(
            "CarrierConnection moet zijn opgeslagen voordat de sleutel wordt versleuteld"
        )
    if not api_key:
        raise CredentialEncryptionError("Lege API-sleutel kan niet worden opgeslagen")
    connection.api_key_encrypted = _encrypt(
        api_key,
        _carrier_aad(
            connection.id, connection.organization_id, connection.carrier, KEY_ID
        ),
    )
    connection.api_key_key_id = KEY_ID


def get_carrier_api_key(connection: "CarrierConnection") -> str | None:
    if not connection.api_key_encrypted:
        return None
    if not connection.api_key_key_id:
        raise CredentialEncryptionError("Vervoerdercredential mist een key-id")
    if connection.api_key_key_id != KEY_ID:
        raise CredentialEncryptionError(
            f"Onbekende credential key-id: {connection.api_key_key_id}"
        )
    return _decrypt(
        connection.api_key_encrypted,
        _carrier_aad(
            connection.id,
            connection.organization_id,
            connection.carrier,
            connection.api_key_key_id,
        ),
        subject="Vervoerdercredential",
    )


def has_carrier_api_key(connection: "CarrierConnection | None") -> bool:
    return bool(
        connection and connection.api_key_encrypted and connection.api_key_key_id
    )
