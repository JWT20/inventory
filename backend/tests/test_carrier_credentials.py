"""The carrier key is stored per organization, encrypted, and never shared."""

import pytest

from app.models import CarrierConnection, Organization
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_carrier_api_key,
    has_carrier_api_key,
    store_access_token,
    store_carrier_api_key,
)
from app.models import ChannelConnection


def _carrier_connection(db, slug="carrier-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders"]
    db.add(org)
    db.flush()
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    return connection


def test_api_key_roundtrip_is_ciphertext_only(db):
    connection = _carrier_connection(db)
    store_carrier_api_key(connection, "cf319563-5baf-49a5-a8a1-000000000000")
    db.commit()

    assert "cf319563" not in connection.api_key_encrypted
    assert connection.api_key_key_id == "v1"
    assert has_carrier_api_key(connection)
    assert get_carrier_api_key(connection) == "cf319563-5baf-49a5-a8a1-000000000000"


def test_key_of_one_merchant_cannot_be_moved_to_another(db):
    first = _carrier_connection(db, "first-merchant")
    second = _carrier_connection(db, "second-merchant")
    store_carrier_api_key(first, "first-merchant-key")

    second.api_key_encrypted = first.api_key_encrypted
    second.api_key_key_id = first.api_key_key_id

    with pytest.raises(CredentialEncryptionError, match="veilig"):
        get_carrier_api_key(second)


def test_channel_token_is_not_usable_as_a_carrier_key(db):
    """Separate AAD namespaces: one ciphertext cannot serve as the other."""
    carrier = _carrier_connection(db, "shared-secret-org")
    channel = ChannelConnection(
        organization_id=carrier.organization_id,
        channel="shopify",
        shop_domain="shared-secret-org.myshopify.com",
    )
    db.add(channel)
    db.flush()
    store_access_token(channel, "shpat_channel_token")

    carrier.api_key_encrypted = channel.access_token_encrypted
    carrier.api_key_key_id = channel.access_token_key_id

    with pytest.raises(CredentialEncryptionError, match="veilig"):
        get_carrier_api_key(carrier)


def test_unstored_connection_cannot_be_encrypted(db):
    org = Organization(name="unsaved", slug="unsaved")
    org.modules = ["inventory"]
    db.add(org)
    db.flush()
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")

    with pytest.raises(CredentialEncryptionError, match="opgeslagen"):
        store_carrier_api_key(connection, "key")


def test_missing_key_reads_as_not_connected(db):
    connection = _carrier_connection(db, "no-key-yet")

    assert has_carrier_api_key(connection) is False
    assert get_carrier_api_key(connection) is None
