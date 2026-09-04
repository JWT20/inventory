"""Binding one merchant's carrier account to its organization."""

from app.models import CarrierConnection
from app.services.channel_credentials import get_carrier_api_key
from app.services.veloyd import VeloydError
from tests.conftest import auth_header


def _connection(db, org_id):
    return (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.organization_id == org_id,
            CarrierConnection.carrier == "veloyd",
        )
        .first()
    )


def test_connect_stores_the_key_encrypted(
    client, db, admin_token, sample_org, monkeypatch
):
    checked = {}

    def _validate(self):
        checked["api_key"] = self.api_key

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.validate_credentials", _validate
    )

    resp = client.post(
        f"/api/channels/veloyd/connect?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
        json={"api_key": "merchant-carrier-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    assert checked["api_key"] == "merchant-carrier-key"
    connection = _connection(db, sample_org.id)
    assert "merchant-carrier-key" not in connection.api_key_encrypted
    assert get_carrier_api_key(connection) == "merchant-carrier-key"


def test_a_rejected_key_is_never_stored(
    client, db, admin_token, sample_org, monkeypatch
):
    """A typo must surface here, not at the shipping-label gate."""

    def _reject(self):
        raise VeloydError("Veloyd API-sleutel is ongeldig of nog niet geactiveerd")

    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.validate_credentials", _reject
    )

    resp = client.post(
        f"/api/channels/veloyd/connect?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
        json={"api_key": "typo-key"},
    )

    assert resp.status_code == 502
    assert _connection(db, sample_org.id) is None


def test_reconnecting_replaces_the_previous_key(
    client, db, admin_token, sample_org, monkeypatch
):
    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.validate_credentials", lambda self: None
    )
    for key in ("first-key", "rotated-key"):
        resp = client.post(
            f"/api/channels/veloyd/connect?organization_id={sample_org.id}",
            headers=auth_header(admin_token),
            json={"api_key": key},
        )
        assert resp.status_code == 200

    assert db.query(CarrierConnection).count() == 1
    assert get_carrier_api_key(_connection(db, sample_org.id)) == "rotated-key"


def test_status_reports_an_unconnected_organization(
    client, admin_token, sample_org
):
    resp = client.get(
        f"/api/channels/veloyd/status?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "carrier": "veloyd",
        "connected": False,
        "base_url": None,
        "updated_at": None,
    }


def test_a_merchant_cannot_bind_a_carrier_account(
    client, owner_token, sample_org, monkeypatch
):
    monkeypatch.setattr(
        "app.services.veloyd.VeloydClient.validate_credentials", lambda self: None
    )

    resp = client.post(
        f"/api/channels/veloyd/connect?organization_id={sample_org.id}",
        headers=auth_header(owner_token),
        json={"api_key": "not-my-call"},
    )

    assert resp.status_code == 403
