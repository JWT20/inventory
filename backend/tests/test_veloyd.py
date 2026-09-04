import httpx
import pytest

from app.config import settings
from app.models import CarrierConnection, Organization, User
from app.services.channel_credentials import store_carrier_api_key
from app.services.veloyd import (
    VeloydClient,
    VeloydError,
    VeloydLabelMismatch,
    VeloydNotConnected,
    client_for_organization,
    clients_for_user,
    verify_veloyd_label,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://app.veloyd.nl")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


def test_veloyd_tracking_lookup_returns_reference_and_tracking(monkeypatch):
    def _get(url, headers, timeout):
        assert url.endswith("/parcel/get/tracktrace/V793AUDS9F4MB")
        assert headers == {"Authorization": "Bearer test-key"}
        return FakeResponse(
            200,
            {
                "parcel": {
                    "reference": "#1262",
                    "trackTrace": "V793AUDS9F4MB",
                    "trackTraceLink": "https://tracking.example/V793AUDS9F4MB",
                    "carrier": "Break Away",
                }
            },
        )

    monkeypatch.setattr("app.services.veloyd.httpx.get", _get)
    label = verify_veloyd_label(
        "V793AUDS9F4MB",
        "1262",
        client=VeloydClient(api_key="test-key"),
    )

    assert label.reference == "1262"
    assert label.shopify_tracking_info == {
        "number": "V793AUDS9F4MB",
        "url": "https://tracking.example/V793AUDS9F4MB",
        "company": "Break Away",
    }


def test_veloyd_valid_label_for_other_order_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {"parcel": {"reference": "1263", "trackTrace": "VOTHER"}},
        ),
    )

    with pytest.raises(VeloydLabelMismatch, match="andere order"):
        verify_veloyd_label(
            "VOTHER", "1262", client=VeloydClient(api_key="test-key")
        )


def test_veloyd_unauthorized_key_has_actionable_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(401, {"error": "User not authorized"}),
    )

    with pytest.raises(VeloydError, match="ongeldig of nog niet geactiveerd"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VTEST")


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (
            400,
            {
                "error": "UserError",
                "description": "Parcel with trackTrace: VUNKNOWN not found",
            },
        ),
        (404, {"error": "NotFound"}),
    ],
)
def test_veloyd_unknown_tracking_is_label_mismatch(
    monkeypatch, status_code, payload
):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(status_code, payload),
    )

    with pytest.raises(VeloydLabelMismatch, match="niet bekend bij Veloyd"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VUNKNOWN")


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (400, {"error": "UserError", "description": "Invalid request"}),
        (500, {"error": "InternalServerError"}),
    ],
)
def test_veloyd_other_upstream_errors_remain_service_errors(
    monkeypatch, status_code, payload
):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(status_code, payload),
    )

    with pytest.raises(VeloydError, match="kon het label niet controleren"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VTEST")


def test_tracking_code_is_looked_up_in_upper_case(monkeypatch):
    """Veloyd matches case-sensitively; a typed-in code is not upper case."""
    seen = {}

    def _get(url, headers, timeout):
        seen["url"] = url
        return FakeResponse(
            200, {"parcel": {"reference": "1262", "trackTrace": "3SIJVT018280390"}}
        )

    monkeypatch.setattr("app.services.veloyd.httpx.get", _get)
    VeloydClient(api_key="test-key").parcel_by_tracking_number(" 3sijvt018280390 ")

    assert seen["url"].endswith("/parcel/get/tracktrace/3SIJVT018280390")


def _organization(db, slug):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders"]
    db.add(org)
    db.flush()
    return org


def test_client_for_organization_uses_the_stored_key(db):
    org = _organization(db, "own-carrier-account")
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, "merchant-own-key")
    db.commit()

    assert client_for_organization(db, org.id).api_key == "merchant-own-key"


def test_only_the_legacy_organization_falls_back_to_the_environment_key(
    db, monkeypatch
):
    """The merchant configured through .env keeps shipping until it is migrated."""
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    org = _organization(db, "not-migrated-yet")
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", org.id)

    assert client_for_organization(db, org.id).api_key == "environment-key"


def test_another_organization_never_borrows_the_environment_key(db, monkeypatch):
    """Borrowing another merchant's account is what per-org keys exist to stop."""
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    legacy = _organization(db, "the-legacy-merchant")
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", legacy.id)
    newcomer = _organization(db, "a-second-merchant")

    with pytest.raises(VeloydNotConnected, match="geen eigen Veloyd-account"):
        client_for_organization(db, newcomer.id)


def test_a_connection_without_a_key_is_not_connected(db, monkeypatch):
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    org = _organization(db, "connected-but-keyless")
    db.add(CarrierConnection(organization_id=org.id, carrier="veloyd"))
    db.commit()

    with pytest.raises(VeloydNotConnected):
        client_for_organization(db, org.id)


def test_creating_a_parcel_never_uses_the_legacy_fallback(db, monkeypatch):
    """Reading a label under a stale account is recoverable; shipping is not."""
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    org = _organization(db, "legacy-may-not-ship")
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", org.id)

    with pytest.raises(VeloydNotConnected):
        client_for_organization(db, org.id, allow_legacy_fallback=False)


def test_client_for_organization_honours_a_stored_base_url(db):
    org = _organization(db, "second-veloyd-install")
    connection = CarrierConnection(
        organization_id=org.id,
        carrier="veloyd",
        base_url="https://staging.veloyd.nl/api/",
    )
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, "staging-key")
    db.commit()

    client = client_for_organization(db, org.id)
    assert client.base_url == "https://staging.veloyd.nl/api"


def test_scanning_member_uses_the_account_of_their_own_merchant(db):
    org = _organization(db, "scanning-merchant")
    connection = CarrierConnection(organization_id=org.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, "scanning-merchant-key")
    user = User(
        username="member-of-merchant",
        email="member@local",
        hashed_password="x",
        role="member",
        organization_id=org.id,
        is_verified=True,
    )
    db.add(user)
    db.commit()

    assert [client.api_key for client in clients_for_user(db, user)] == [
        "scanning-merchant-key"
    ]


def test_a_courier_may_resolve_a_label_against_every_connected_merchant(
    db, monkeypatch
):
    """A courier rides for several merchants and has no account of their own."""
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    first = _organization(db, "merchant-one")
    connection = CarrierConnection(organization_id=first.id, carrier="veloyd")
    db.add(connection)
    db.flush()
    store_carrier_api_key(connection, "merchant-one-key")
    legacy = _organization(db, "merchant-on-env")
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", legacy.id)
    courier = User(
        username="riding-courier",
        email="riding-courier@local",
        hashed_password="x",
        role="courier",
        is_verified=True,
    )
    db.add(courier)
    db.commit()

    keys = [client.api_key for client in clients_for_user(db, courier)]

    assert keys == ["merchant-one-key", "environment-key"]


def test_a_courier_gets_nothing_when_no_merchant_is_connected(db, monkeypatch):
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", None)
    courier = User(
        username="unconnected-courier",
        email="unconnected-courier@local",
        hashed_password="x",
        role="courier",
        is_verified=True,
    )
    db.add(courier)
    db.commit()

    assert clients_for_user(db, courier) == []
