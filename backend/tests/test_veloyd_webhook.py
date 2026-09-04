"""Veloyd reporting a printed label, over a URL that is its own credential."""

import pytest

from app.config import settings
from app.models import (
    CarrierConnection,
    Order,
    OrderDeliveryAddress,
    OrderLine,
    OrderParcel,
    Organization,
    SKU,
)
from app.services.veloyd_webhook import hash_webhook_token
from tests.conftest import auth_header

WEBHOOK_URL = "/api/integrations/veloyd/webhook"


def _carrier(db, org, token: str | None = "secret-token") -> CarrierConnection:
    connection = CarrierConnection(
        organization_id=org.id,
        carrier="veloyd",
        webhook_token_hash=hash_webhook_token(token) if token else None,
    )
    db.add(connection)
    db.commit()
    return connection


def _parcel(db, org, *, parcel_id="veloyd-1", sequence=1) -> OrderParcel:
    sku = SKU(
        sku_code=f"WIJN-{parcel_id}",
        name="Wijn",
        organization_id=org.id,
        is_bottle=True,
        source_product_id=f"prd_{parcel_id}",
    )
    db.add(sku)
    db.flush()
    order = Order(
        organization_id=org.id,
        channel="advice",
        external_id=f"ext-{parcel_id}",
        reference=f"ADV-{parcel_id.upper()}",
        channel_reference="JUR-2026-8DP6QY",
        status="active",
    )
    db.add(order)
    db.flush()
    db.add(OrderLine(order_id=order.id, sku_id=sku.id, quantity=2))
    db.add(
        OrderDeliveryAddress(
            order_id=order.id,
            recipient_name="Pieter Haarman",
            street="Theophile de Bockstraat",
            house_number="89",
            postal_code="1058VB",
            city="Amsterdam",
            country="NL",
        )
    )
    parcel = OrderParcel(
        order_id=order.id, sequence=sequence, veloyd_parcel_id=parcel_id
    )
    db.add(parcel)
    db.commit()
    db.refresh(parcel)
    return parcel


def test_a_printed_label_links_its_tracking_code(client, db, sample_org):
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org)

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={
            "parcel": {
                "id": "veloyd-1",
                "trackTrace": "3SIJVT018280390",
                "trackTraceLink": "https://jouw.postnl.nl/track-and-trace/3SIJVT018280390",
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json()["result"] == "linked"
    db.refresh(parcel)
    assert parcel.tracking_code == "3sijvt018280390"
    assert parcel.tracking_url.endswith("3SIJVT018280390")
    # The print is the moment the parcel stopped being cancellable.
    assert parcel.label_printed_at is not None


def test_an_unwrapped_body_is_understood_too(client, db, sample_org):
    """Veloyd's webhook shape is undocumented; both layouts must land."""
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org, parcel_id="veloyd-flat")

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"id": "veloyd-flat", "trackTrace": "VBTAAZZSD4DH9"},
    )

    assert resp.json()["result"] == "linked"
    db.refresh(parcel)
    assert parcel.tracking_code == "vbtaazzsd4dh9"


def test_a_repeated_event_changes_nothing(client, db, sample_org):
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org)
    body = {"parcel": {"id": "veloyd-1", "trackTrace": "3SIJVT018280390"}}

    client.post(f"{WEBHOOK_URL}/secret-token", json=body)
    db.refresh(parcel)
    printed_at = parcel.label_printed_at

    again = client.post(f"{WEBHOOK_URL}/secret-token", json=body)

    assert again.json()["result"] == "unchanged"
    db.refresh(parcel)
    assert parcel.label_printed_at == printed_at


def test_a_wrong_secret_is_not_told_it_is_wrong(client, db, sample_org):
    _carrier(db, sample_org)
    _parcel(db, sample_org)

    resp = client.post(
        f"{WEBHOOK_URL}/not-the-token",
        json={"parcel": {"id": "veloyd-1", "trackTrace": "3SIJVT018280390"}},
    )

    assert resp.status_code == 404


def test_another_merchants_parcel_is_dropped(client, db, sample_org):
    """The carrier's account holds several merchants; only ours may be touched."""
    other = Organization(name="Racesokken", slug="racesokken-webhook")
    other.modules = ["inventory", "orders"]
    db.add(other)
    db.commit()
    _carrier(db, sample_org)
    foreign = _parcel(db, other, parcel_id="veloyd-other")

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "veloyd-other", "trackTrace": "3SIJVT018280391"}},
    )

    assert resp.status_code == 200
    assert resp.json()["result"] == "ignored_unknown_parcel"
    db.refresh(foreign)
    assert foreign.tracking_code is None


def test_an_event_about_a_parcel_we_never_made_is_dropped(client, db, sample_org):
    _carrier(db, sample_org)

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "made-in-veloyd-ui", "trackTrace": "3SIJVT018280392"}},
    )

    assert resp.json()["result"] == "ignored_unknown_parcel"


def test_an_event_without_a_tracking_code_waits(client, db, sample_org):
    """A parcel is created before it is printed; that event carries no code."""
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org)

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token", json={"parcel": {"id": "veloyd-1", "status": 1}}
    )

    assert resp.json()["result"] == "ignored_without_tracking_code"
    db.refresh(parcel)
    assert parcel.tracking_code is None
    assert parcel.label_printed_at is None


def test_a_code_that_already_belongs_to_another_box_is_refused(
    client, db, sample_org
):
    _carrier(db, sample_org)
    first = _parcel(db, sample_org, parcel_id="veloyd-1")
    _parcel(db, sample_org, parcel_id="veloyd-2", sequence=1)
    first.tracking_code = "3sijvt018280390"
    db.commit()

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "veloyd-2", "trackTrace": "3SIJVT018280390"}},
    )

    assert resp.json()["result"] == "conflict"


def test_the_url_is_issued_once_and_rotates(
    client, db, admin_token, sample_org, monkeypatch
):
    monkeypatch.setattr(settings, "domain", "dockscan.example")
    connection = _carrier(db, sample_org, token=None)

    first = client.post(
        f"/api/channels/veloyd/webhook-url?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert first.status_code == 200
    url = first.json()["url"]
    assert url.startswith(
        "https://dockscan.example/api/integrations/veloyd/webhook/"
    )
    token = url.rsplit("/", 1)[1]
    db.refresh(connection)
    # Only the digest is kept: a database leak must not yield a working URL.
    assert token not in (connection.webhook_token_hash or "")
    assert connection.webhook_token_hash == hash_webhook_token(token)

    second = client.post(
        f"/api/channels/veloyd/webhook-url?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )
    assert second.json()["url"] != url

    stale = client.post(
        f"{WEBHOOK_URL}/{token}", json={"parcel": {"id": "veloyd-1"}}
    )
    assert stale.status_code == 404


def test_a_url_cannot_be_issued_before_the_account_is_connected(
    client, db, admin_token, sample_org, monkeypatch
):
    monkeypatch.setattr(settings, "domain", "dockscan.example")

    resp = client.post(
        f"/api/channels/veloyd/webhook-url?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
    )

    assert resp.status_code == 409


def test_a_merchant_cannot_issue_a_webhook_url(
    client, db, owner_token, sample_org, monkeypatch
):
    monkeypatch.setattr(settings, "domain", "dockscan.example")
    _carrier(db, sample_org, token=None)

    resp = client.post(
        f"/api/channels/veloyd/webhook-url?organization_id={sample_org.id}",
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 403


def test_a_later_event_still_fills_in_the_tracking_link(client, db, sample_org):
    """Veloyd may report the code first and the link only afterwards."""
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org)

    client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "veloyd-1", "trackTrace": "3SIJVT018280390"}},
    )
    db.refresh(parcel)
    assert parcel.tracking_url is None
    printed_at = parcel.label_printed_at

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={
            "parcel": {
                "id": "veloyd-1",
                "trackTrace": "3SIJVT018280390",
                "trackTraceLink": "https://jouw.postnl.nl/track-and-trace/3SIJVT018280390",
            }
        },
    )

    assert resp.json()["result"] == "linked"
    db.refresh(parcel)
    assert parcel.tracking_url.endswith("3SIJVT018280390")
    # The print moment is the one thing a later event may not move.
    assert parcel.label_printed_at == printed_at


def test_a_second_code_for_the_same_box_is_refused(client, db, sample_org):
    """Overwriting would strand the label that is already on the box."""
    _carrier(db, sample_org)
    parcel = _parcel(db, sample_org)
    client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "veloyd-1", "trackTrace": "3SIJVT018280390"}},
    )

    resp = client.post(
        f"{WEBHOOK_URL}/secret-token",
        json={"parcel": {"id": "veloyd-1", "trackTrace": "VSOMETHINGELSE"}},
    )

    assert resp.json()["result"] == "conflict"
    db.refresh(parcel)
    assert parcel.tracking_code == "3sijvt018280390"
