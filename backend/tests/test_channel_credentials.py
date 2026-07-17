import base64

import pytest

from app.config import settings
from app.models import ChannelConnection, Organization
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_access_token,
    store_access_token,
)


def _connection(db, slug="encrypted-shop"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "channel_orders"]
    db.add(org)
    db.flush()
    connection = ChannelConnection(
        organization_id=org.id,
        channel="shopify",
        shop_domain=f"{slug}.myshopify.com",
    )
    db.add(connection)
    db.flush()
    return connection


def test_token_roundtrip_is_ciphertext_only(db):
    connection = _connection(db)
    store_access_token(connection, "shpat_never-store-me-plain")
    db.commit()

    assert "shpat_" not in connection.access_token_encrypted
    assert connection.access_token_key_id == "v1"
    assert get_access_token(connection) == "shpat_never-store-me-plain"


def test_tampered_ciphertext_fails_closed(db):
    connection = _connection(db, "tampered-shop")
    store_access_token(connection, "shpat_test")
    connection.access_token_encrypted = connection.access_token_encrypted[:-2] + "AA"

    with pytest.raises(CredentialEncryptionError, match="veilig"):
        get_access_token(connection)


def test_ciphertext_is_bound_to_connection_and_organization(db):
    first = _connection(db, "first-shop")
    second = _connection(db, "second-shop")
    store_access_token(first, "shpat_first")
    second.access_token_encrypted = first.access_token_encrypted
    second.access_token_key_id = first.access_token_key_id

    with pytest.raises(CredentialEncryptionError, match="veilig"):
        get_access_token(second)


def test_wrong_runtime_key_fails_closed(db, monkeypatch):
    connection = _connection(db, "wrong-key-shop")
    store_access_token(connection, "shpat_test")
    other_key = base64.urlsafe_b64encode(b"another-channel-key-material-32!").decode()
    monkeypatch.setattr(settings, "channel_credential_encryption_key", other_key)

    with pytest.raises(CredentialEncryptionError, match="veilig"):
        get_access_token(connection)
