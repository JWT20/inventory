"""bol Retailer API adapter and Admin connection tests (read-only phase)."""
import datetime

import pytest

import app.routers.channels as channels_mod
import app.services.bol as bol_mod
from app.models import ChannelConnection, Order, Organization, SKU
from app.services.bol import (
    BolAPIError,
    BolClient,
    clear_token_cache,
    sync_bol,
    to_normalized,
)
from tests.conftest import auth_header


def _payload(order_id="A2K8290LP8", ean="8710000000001"):
    return {
        "orderId": order_id,
        "orderPlacedDateTime": "2026-07-18T09:00:00+02:00",
        "shipmentDetails": {"firstName": "Test", "surname": "Klant"},
        "orderItems": [
            {
                "orderItemId": f"{order_id}-1",
                "product": {"ean": ean, "title": "Racesok"},
                "quantity": 3,
                "quantityShipped": 1,
                "quantityCancelled": 1,
                "latestChangedDateTime": "2026-07-18T10:00:00+02:00",
                "fulfilment": {
                    "method": "FBR",
                    "distributionParty": "BOL",
                },
            }
        ],
    }


class _Response:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _HTTP:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Response(
            200,
            {"access_token": "temporary-bearer", "expires_in": 599, "token_type": "Bearer"},
        )

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if url.endswith("/orders") and kwargs["params"]["page"] == 1:
            return _Response(200, {"orders": [{"orderId": "A2K8290LP8"}]})
        if url.endswith("/orders"):
            return _Response(200, {"orders": []})
        if url.endswith("/orders/A2K8290LP8"):
            return _Response(200, _payload())
        raise AssertionError(f"unexpected URL: {url}")


class _FakeBolClient:
    configured = True

    def __init__(self, payloads=None):
        self.payloads = payloads or []
        self.validated = False

    def validate_credentials(self):
        self.validated = True

    def fetch_open_orders(self):
        yield from self.payloads


def _org(db, slug):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _configure_bol_settings(monkeypatch):
    monkeypatch.setattr(channels_mod.settings, "bol_client_id", "client-id")
    monkeypatch.setattr(channels_mod.settings, "bol_client_secret", "client-secret")
    monkeypatch.setattr(channels_mod.settings, "bol_token_url", "https://login.bol.test/token")
    monkeypatch.setattr(
        channels_mod.settings,
        "bol_api_base_url",
        "https://api.bol.test/retailer",
    )


def test_client_reuses_one_token_and_fetches_full_paginated_orders():
    clear_token_cache()
    http = _HTTP()
    client = BolClient(
        client_id="client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
        http_client=http,
    )

    assert [order["orderId"] for order in client.fetch_open_orders()] == ["A2K8290LP8"]
    assert len(http.posts) == 1
    assert len(http.gets) == 3
    list_params = http.gets[0][1]["params"]
    assert list_params == {"fulfilment-method": "FBR", "status": "OPEN", "page": 1}
    assert http.gets[1][1]["headers"]["Authorization"] == "Bearer temporary-bearer"


def test_client_does_not_allocate_a_persistent_httpx_client(monkeypatch):
    def fail_if_constructed(*args, **kwargs):
        pytest.fail("BolClient must not own an unclosed persistent httpx.Client")

    monkeypatch.setattr(bol_mod.httpx, "Client", fail_if_constructed)

    client = BolClient(
        client_id="client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
    )

    assert client.configured is True


def test_token_request_does_not_hold_cache_lock():
    clear_token_cache()

    class LockCheckingHTTP(_HTTP):
        def post(self, url, **kwargs):
            acquired = bol_mod._token_lock.acquire(blocking=False)
            assert acquired is True
            bol_mod._token_lock.release()
            return super().post(url, **kwargs)

    client = BolClient(
        client_id="lock-client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
        http_client=LockCheckingHTTP(),
    )

    client.validate_credentials()


def test_open_order_pagination_has_a_safety_limit(monkeypatch):
    client = BolClient(
        client_id="client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
        http_client=_HTTP(),
    )
    monkeypatch.setattr(bol_mod, "_MAX_ORDER_PAGES", 2)

    def endless_orders(path, *, params=None):
        if path == "/orders":
            return {"orders": [{"orderId": f"order-{params['page']}"}]}
        return {"orderId": path.rsplit("/", 1)[-1], "orderItems": []}

    monkeypatch.setattr(client, "_get", endless_orders)

    with pytest.raises(BolAPIError, match="veiligheidslimiet van 2 pagina's"):
        list(client.fetch_open_orders())


def test_bol_status_checks_settings_without_constructing_client(monkeypatch):
    _configure_bol_settings(monkeypatch)
    monkeypatch.setattr(
        channels_mod,
        "BolClient",
        lambda: pytest.fail("status checks must not construct BolClient"),
    )
    connection = ChannelConnection(channel="bol", mode="observe", status="active")

    assert channels_mod._bol_status_for(connection).connected is True


def test_to_normalized_does_not_count_cancelled_units_as_shipped():
    normalized = to_normalized(_payload())

    assert normalized.external_id == "A2K8290LP8"
    assert normalized.reference == "A2K8290LP8"
    assert normalized.customer_name == "Test Klant"
    assert normalized.financial_status == "paid"
    assert normalized.fulfillment_status == "partially_fulfilled"
    assert normalized.ordered_at == datetime.datetime.fromisoformat(
        "2026-07-18T09:00:00+02:00"
    )
    assert len(normalized.lines) == 1
    assert normalized.lines[0].quantity == 2
    assert normalized.lines[0].unfulfilled_quantity == 1
    assert normalized.lines[0].external_id == "A2K8290LP8-1"


def test_sync_bol_imports_idempotently_in_observe_mode(db):
    org = _org(db, "bol-sync")
    sku = SKU(
        sku_code="BOL-1",
        name="Bol sok",
        organization_id=org.id,
        product_type="barcode",
        ean="8710000000001",
    )
    connection = ChannelConnection(
        organization_id=org.id, channel="bol", mode="observe", status="active"
    )
    db.add_all([sku, connection])
    db.commit()

    first = sync_bol(db, connection, _FakeBolClient([_payload()]))
    db.commit()
    second = sync_bol(db, connection, _FakeBolClient([_payload()]))
    db.commit()

    assert (first.fetched, first.created, first.updated, first.unmatched) == (1, 1, 0, 0)
    assert (second.fetched, second.created, second.updated, second.unmatched) == (1, 0, 1, 0)
    assert db.query(Order).filter_by(channel="bol", external_id="A2K8290LP8").count() == 1
    order = db.query(Order).filter_by(channel="bol", external_id="A2K8290LP8").one()
    assert order.status == "observed"
    assert connection.last_synced_at is not None


def test_admin_can_connect_and_sync_bol(client, db, admin_token, sample_org, monkeypatch):
    _configure_bol_settings(monkeypatch)
    fake = _FakeBolClient([_payload()])
    monkeypatch.setattr(channels_mod, "BolClient", lambda: fake)
    db.add(
        SKU(
            sku_code="BOL-API",
            name="Bol API sok",
            organization_id=sample_org.id,
            product_type="barcode",
            ean="8710000000001",
        )
    )
    db.commit()

    connect = client.post(
        f"/api/channels/bol/connect?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert connect.status_code == 200
    assert connect.json()["connected"] is True
    assert connect.json()["mode"] == "observe"
    assert fake.validated is True

    sync = client.post(
        f"/api/channels/bol/sync?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert sync.status_code == 200
    assert sync.json() == {"fetched": 1, "created": 1, "updated": 0, "unmatched": 0}

    recon = client.get(
        f"/api/channels/bol/reconciliation?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert recon.status_code == 200
    assert recon.json()["orders"][0]["external_id"] == "A2K8290LP8"


def test_bol_connection_is_platform_admin_only(client, owner_token, sample_org, monkeypatch):
    monkeypatch.setattr(channels_mod, "BolClient", lambda: _FakeBolClient())
    response = client.post(
        f"/api/channels/bol/connect?organization_id={sample_org.id}",
        headers=auth_header(owner_token),
    )
    assert response.status_code == 403


def test_single_env_account_cannot_bind_to_two_organizations(
    client, db, admin_token, sample_org, monkeypatch
):
    _configure_bol_settings(monkeypatch)
    monkeypatch.setattr(channels_mod, "BolClient", lambda: _FakeBolClient())
    first = client.post(
        f"/api/channels/bol/connect?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert first.status_code == 200

    other = _org(db, "bol-other")
    second = client.post(
        f"/api/channels/bol/connect?organization_id={other.id}",
        headers=auth_header(admin_token),
    )
    assert second.status_code == 409
