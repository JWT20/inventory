"""bol Retailer API adapter and Admin connection tests (read-only phase)."""
import datetime

import pytest

import app.routers.channels as channels_mod
import app.services.bol as bol_mod
from app.models import ChannelConnection, InventoryBalance, Order, Organization, SKU
from app.services.bol import (
    BolAPIError,
    BolClient,
    BolOrderBatch,
    BolOrderUpdate,
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
        self.patches = []

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

    def patch(self, url, **kwargs):
        self.patches.append((url, kwargs))
        return _Response(200, {"offerId": url.rsplit("/", 1)[-1]})


class _FakeBolClient:
    configured = True

    def __init__(
        self, payloads=None, shipped_at_by_order=None, newest_shipment_at=None
    ):
        self.payloads = payloads or []
        self.shipped_at_by_order = shipped_at_by_order or {}
        self.newest_shipment_at = newest_shipment_at
        self.validated = False
        self.shipped_since = None

    def validate_credentials(self):
        self.validated = True

    def fetch_order_updates(self, *, shipped_since=None):
        self.shipped_since = shipped_since
        return BolOrderBatch(
            orders=[
                BolOrderUpdate(
                    payload=payload,
                    shipped_at=self.shipped_at_by_order.get(payload["orderId"]),
                )
                for payload in self.payloads
            ],
            newest_shipment_at=self.newest_shipment_at,
        )


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


def test_client_resolves_fbr_offer_and_updates_stock_with_v11():
    clear_token_cache()

    class OfferHTTP(_HTTP):
        def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            assert url.endswith("/offers")
            return _Response(
                200,
                {
                    "offers": [
                        {
                            "offerId": "offer-fbb",
                            "ean": "8710000000001",
                            "fulfilment": {"method": "FBB"},
                        },
                        {
                            "offerId": "offer-fbr",
                            "ean": "8710000000001",
                            "fulfilment": {"method": "FBR"},
                        },
                    ],
                    "page": {"nextCursor": None},
                },
            )

    http = OfferHTTP()
    client = BolClient(
        client_id="offer-client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
        http_client=http,
    )

    assert client.find_fbr_offer_ids("8710000000001") == ["offer-fbr"]
    client.set_offer_stock("offer-fbr", 7)

    assert http.gets[0][1]["params"] == {
        "eans": "8710000000001",
        "page-size": 100,
    }
    assert http.gets[0][1]["headers"]["Accept"] == "application/vnd.retailer.v11+json"
    assert http.patches[0][1]["json"] == {
        "stock": {"amount": 7, "managedByRetailer": True}
    }
    assert http.patches[0][1]["headers"]["Content-Type"] == (
        "application/vnd.retailer.v11+json"
    )


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


def test_fetch_order_updates_combines_open_and_shipped_orders_once(monkeypatch):
    client = BolClient(
        client_id="client-id",
        client_secret="client-secret",
        token_url="https://login.bol.test/token",
        api_base_url="https://api.bol.test/retailer",
        http_client=_HTTP(),
    )
    shipped_at = datetime.datetime.fromisoformat("2026-07-19T12:30:00+02:00")
    calls = []

    def api_get(path, *, params=None):
        calls.append((path, params))
        if path == "/shipments" and params["page"] == 1:
            return {
                "shipments": [
                    {
                        "shipmentDateTime": shipped_at.isoformat(),
                        "order": {"orderId": "SHIPPED-1"},
                    },
                    {
                        "shipmentDateTime": "2026-07-19T11:00:00+02:00",
                        "order": {"orderId": "SHIPPED-1"},
                    },
                ]
            }
        if path == "/shipments":
            return {"shipments": []}
        if path == "/orders" and params["page"] == 1:
            return {"orders": [{"orderId": "OPEN-1"}]}
        if path == "/orders":
            return {"orders": []}
        return _payload(path.rsplit("/", 1)[-1])

    monkeypatch.setattr(client, "_get", api_get)

    batch = client.fetch_order_updates()

    assert [update.payload["orderId"] for update in batch.orders] == [
        "OPEN-1",
        "SHIPPED-1",
    ]
    assert batch.orders[0].shipped_at is None
    assert batch.orders[1].shipped_at == shipped_at.astimezone(datetime.timezone.utc)
    assert batch.newest_shipment_at == shipped_at.astimezone(datetime.timezone.utc)
    detail_calls = [path for path, _ in calls if path.startswith("/orders/")]
    assert detail_calls == ["/orders/OPEN-1", "/orders/SHIPPED-1"]


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


def test_sync_bol_live_makes_open_order_pickable_and_reserves(db):
    org = _org(db, "bol-live-sync")
    sku = SKU(
        sku_code="BOL-LIVE",
        name="Live bol-sok",
        organization_id=org.id,
        product_type="barcode",
        ean="8710000000001",
    )
    connection = ChannelConnection(
        organization_id=org.id,
        channel="bol",
        mode="live",
        status="active",
        inventory_authority_started_at=datetime.datetime(2026, 7, 17),
    )
    db.add_all([sku, connection])
    db.commit()

    payload = _payload()
    payload["orderItems"][0]["quantityShipped"] = 0
    payload["orderItems"][0]["quantityCancelled"] = 0
    summary = sync_bol(db, connection, _FakeBolClient([payload]))
    db.commit()

    order = db.query(Order).filter_by(channel="bol", external_id="A2K8290LP8").one()
    balance = db.query(InventoryBalance).filter_by(
        organization_id=org.id, sku_id=sku.id
    ).one()
    assert order.status == "active"
    assert balance.quantity_reserved == 3
    assert summary.reserved_sku_ids == {sku.id}


def test_sync_bol_persists_shipped_order_in_same_observe_list(db):
    org = _org(db, "bol-shipped")
    db.add(
        SKU(
            sku_code="BOL-SHIPPED",
            name="Verzonden bol-product",
            organization_id=org.id,
            product_type="barcode",
            ean="8710000000001",
        )
    )
    connection = ChannelConnection(
        organization_id=org.id, channel="bol", mode="observe", status="active"
    )
    db.add(connection)
    db.commit()

    payload = _payload("SHIPPED-1")
    payload["orderItems"][0]["quantityShipped"] = 3
    payload["orderItems"][0]["quantityCancelled"] = 0
    shipped_at = datetime.datetime.fromisoformat("2026-07-19T12:30:00+02:00")
    newest = shipped_at.astimezone(datetime.timezone.utc)
    fake = _FakeBolClient(
        [payload],
        shipped_at_by_order={"SHIPPED-1": newest},
        newest_shipment_at=newest,
    )

    summary = sync_bol(db, connection, fake)
    db.commit()

    order = db.query(Order).filter_by(channel="bol", external_id="SHIPPED-1").one()
    assert (summary.fetched, summary.created, summary.unmatched) == (1, 1, 0)
    assert order.status == "observed"
    assert order.channel_fulfillment_status == "fulfilled"
    stored_shipped_at = order.channel_shipped_at
    if stored_shipped_at.tzinfo is None:
        stored_shipped_at = stored_shipped_at.replace(tzinfo=datetime.timezone.utc)
    assert stored_shipped_at == newest
    assert connection.cursor == newest.isoformat()

    older = newest - datetime.timedelta(minutes=1)
    sync_bol(
        db,
        connection,
        _FakeBolClient(
            [payload],
            shipped_at_by_order={"SHIPPED-1": older},
            newest_shipment_at=older,
        ),
    )
    db.commit()
    db.refresh(order)
    db.refresh(connection)
    stored_shipped_at = order.channel_shipped_at
    if stored_shipped_at.tzinfo is None:
        stored_shipped_at = stored_shipped_at.replace(tzinfo=datetime.timezone.utc)
    assert stored_shipped_at == newest
    assert connection.cursor == newest.isoformat()


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


def test_admin_can_promote_bol_from_observe_to_live(
    client, db, admin_token, sample_org, monkeypatch
):
    _configure_bol_settings(monkeypatch)
    sku = SKU(
        sku_code="BOL-CUTOVER",
        name="Bol cutover sok",
        organization_id=sample_org.id,
        product_type="barcode",
        ean="8710000000001",
    )
    connection = ChannelConnection(
        organization_id=sample_org.id,
        channel="bol",
        mode="observe",
        status="active",
    )
    db.add_all([sku, connection])
    db.commit()
    sync_bol(db, connection, _FakeBolClient([_payload()]))
    db.commit()

    fake = _FakeBolClient([_payload()])
    monkeypatch.setattr(channels_mod, "BolClient", lambda: fake)
    monkeypatch.setattr(
        channels_mod, "push_inventory_to_channels", lambda *args, **kwargs: None
    )

    response = client.post(
        f"/api/channels/bol/mode?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
        json={"mode": "live"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "live"
    db.refresh(connection)
    order = db.query(Order).filter_by(channel="bol", external_id="A2K8290LP8").one()
    balance = db.query(InventoryBalance).filter_by(
        organization_id=sample_org.id, sku_id=sku.id
    ).one()
    assert connection.inventory_authority_started_at is not None
    assert order.status == "active"
    assert balance.quantity_reserved == 1


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
