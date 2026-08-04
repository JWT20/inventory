"""Tests for POST /skus/advice-sync — the manual advice-product pull."""

import pytest

from app.config import settings
from app.models import SKU
from app.services import advice_products as advice_products_service
from app.services.advice_products import (
    AdviceProduct,
    AdviceProductSnapshot,
    AdviceProductSyncError,
    advice_sync_slot,
)
from tests.conftest import auth_header


def _product(source_product_id="prd-1", name="Grand Vin", active=True, image_url=None):
    return AdviceProduct(
        source_product_id=source_product_id,
        producer="Château Test",
        name=name,
        vintage="2024",
        color="red",
        active=active,
        image_url=image_url,
    )


class FakeClient:
    """Stands in for AdviceProductsClient; fails loudly on image downloads."""

    def __init__(self, products=None, error=None):
        self.snapshot = AdviceProductSnapshot(products=products or [_product()])
        self.error = error

    def fetch_snapshot(self):
        if self.error:
            raise self.error
        return self.snapshot

    def download_image(self, url: str):
        raise AssertionError(f"unexpected image download: {url}")


@pytest.fixture
def advice_feed(monkeypatch, sample_org):
    """Point the feed config at sample_org so the endpoint is enabled."""
    monkeypatch.setattr(settings, "advice_products_base_url", "https://advies.example")
    monkeypatch.setattr(settings, "advice_products_api_key", "secret")
    monkeypatch.setattr(settings, "advice_stock_organization_id", sample_org.id)
    monkeypatch.setattr(settings, "advice_products_sync_interval_seconds", 3600)
    return sample_org


def _use_client(monkeypatch, client_obj):
    monkeypatch.setattr(
        "app.routers.skus.AdviceProductsClient", lambda *a, **kw: client_obj
    )


def _post(client, token):
    return client.post("/api/skus/advice-sync", headers=auth_header(token))


def test_sync_creates_the_bottle_and_reports_it(
    client, db, monkeypatch, advice_feed, merchant_token
):
    _use_client(monkeypatch, FakeClient())

    resp = _post(client, merchant_token)

    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    assert resp.json()["received"] == 1
    bottle = db.query(SKU).filter(SKU.source_product_id == "prd-1").one()
    assert bottle.is_bottle is True


def test_sync_skips_images_so_the_button_stays_fast(
    client, monkeypatch, advice_feed, merchant_token
):
    # FakeClient.download_image raises, so a passing call proves the endpoint
    # asked for import_images=False. The periodic loop imports it later.
    _use_client(
        monkeypatch, FakeClient([_product(image_url="https://advies.example/i.jpg")])
    )

    assert _post(client, merchant_token).status_code == 200


def test_sync_requires_the_feed_to_be_configured(client, monkeypatch, merchant_token):
    monkeypatch.setattr(settings, "advice_products_base_url", "")

    assert _post(client, merchant_token).status_code == 503


def test_sync_rejects_another_organization(
    client, monkeypatch, advice_feed, merchant_token
):
    monkeypatch.setattr(settings, "advice_stock_organization_id", advice_feed.id + 999)

    assert _post(client, merchant_token).status_code == 403


def test_sync_rejects_a_customer(client, monkeypatch, advice_feed, customer_token):
    _use_client(monkeypatch, FakeClient())

    assert _post(client, customer_token).status_code == 403


def test_sync_reports_a_running_sync_instead_of_racing(
    client, monkeypatch, advice_feed, merchant_token
):
    _use_client(monkeypatch, FakeClient())

    with advice_sync_slot():
        resp = _post(client, merchant_token)

    assert resp.status_code == 409


def test_sync_surfaces_a_feed_failure(
    client, monkeypatch, advice_feed, merchant_token
):
    _use_client(
        monkeypatch,
        FakeClient(error=AdviceProductSyncError("Adviesproductfeed is leeg")),
    )

    resp = _post(client, merchant_token)

    assert resp.status_code == 502
    assert "leeg" in resp.json()["detail"]


def test_periodic_sync_yields_to_a_running_sync(monkeypatch, advice_feed):
    calls = []
    monkeypatch.setattr(
        advice_products_service,
        "sync_advice_products",
        lambda *a, **kw: calls.append(1),
    )

    with advice_sync_slot():
        assert advice_products_service.sync_advice_products_once() is None

    assert calls == []
