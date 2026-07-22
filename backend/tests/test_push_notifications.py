"""Web Push subscription, routing, deduplication, and outbox tests."""

import datetime
from types import SimpleNamespace

from pywebpush import WebPushException

from app.config import settings
from app.models import (
    ChannelConnection,
    Customer,
    CustomerSKU,
    PushDelivery,
    PushSubscription,
    SKU,
)
from app.services.channel_import import (
    NormalizedChannelOrder,
    NormalizedLine,
    import_channel_order,
)
from app.services import push_notifications
from tests.conftest import TestingSessionLocal, auth_header


def _subscription(db, user, suffix: str) -> PushSubscription:
    subscription = PushSubscription(
        user_id=user.id,
        endpoint=f"https://push.example/{suffix}",
        p256dh=f"p256dh-{suffix}",
        auth=f"auth-{suffix}",
    )
    db.add(subscription)
    db.commit()
    return subscription


def _enable_push(monkeypatch) -> None:
    monkeypatch.setattr(settings, "vapid_public_key", "public-test-key")
    monkeypatch.setattr(settings, "vapid_private_key", "private-test-key")
    monkeypatch.setattr(settings, "vapid_subject", "https://example.test")
    monkeypatch.setattr(settings, "push_dispatch_interval_seconds", 5)


def test_subscription_can_be_enabled_and_disabled(
    client, db, owner_user, owner_token, monkeypatch
):
    _enable_push(monkeypatch)

    config = client.get("/api/push/config", headers=auth_header(owner_token))
    assert config.status_code == 200
    assert config.json() == {"enabled": True, "public_key": "public-test-key"}

    payload = {
        "endpoint": "https://push.example/browser-1",
        "keys": {"p256dh": "browser-key", "auth": "browser-auth"},
    }
    response = client.post(
        "/api/push/subscriptions", json=payload, headers=auth_header(owner_token)
    )
    assert response.status_code == 204
    subscription = db.query(PushSubscription).one()
    assert subscription.user_id == owner_user.id
    assert subscription.p256dh == "browser-key"

    response = client.request(
        "DELETE",
        "/api/push/subscriptions",
        json={"endpoint": payload["endpoint"]},
        headers=auth_header(owner_token),
    )
    assert response.status_code == 204
    assert db.query(PushSubscription).count() == 0


def test_customer_order_notifies_merchant_only(
    client,
    db,
    sample_org,
    owner_user,
    customer_user,
    customer_token,
):
    _subscription(db, owner_user, "merchant")
    customer = Customer(name="Restaurant De Test", organization_id=sample_org.id)
    sku = SKU(sku_code="PUSH-WINE-1", name="Push wijn")
    db.add_all([customer, sku])
    db.commit()
    customer_user.customer_id = customer.id
    db.add(CustomerSKU(customer_id=customer.id, sku_id=sku.id))
    db.commit()

    response = client.post(
        "/api/orders",
        json={"lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 2}]},
        headers=auth_header(customer_token),
    )

    assert response.status_code == 200
    delivery = db.query(PushDelivery).one()
    assert delivery.title == "Nieuwe order"
    assert "Restaurant De Test" in delivery.body
    assert delivery.url == f"/?page=orders&order={response.json()['id']}"


def test_merchant_created_order_does_not_notify_merchant(
    client, db, sample_org, owner_user, owner_token
):
    _subscription(db, owner_user, "merchant-self")
    customer = Customer(name="Eigen order", organization_id=sample_org.id)
    sku = SKU(sku_code="PUSH-WINE-2", name="Eigen wijn")
    db.add_all([customer, sku])
    db.commit()

    response = client.post(
        "/api/orders",
        json={
            "organization_id": sample_org.id,
            "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 1}],
        },
        headers=auth_header(owner_token),
    )

    assert response.status_code == 200
    assert db.query(PushDelivery).count() == 0


def test_courier_is_notified_only_when_approved_order_becomes_active(
    client, db, sample_org, owner_token, courier_user
):
    _subscription(db, courier_user, "courier-approved")
    customer = Customer(name="Restaurant", organization_id=sample_org.id)
    sku = SKU(sku_code="PUSH-WINE-3", name="Wijn zonder foto")
    db.add_all([customer, sku])
    db.commit()
    response = client.post(
        "/api/orders",
        json={
            "organization_id": sample_org.id,
            "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 1}],
        },
        headers=auth_header(owner_token),
    )
    order_id = response.json()["id"]

    first = client.post(
        f"/api/orders/{order_id}/approve", headers=auth_header(owner_token)
    )
    assert first.json()["status"] == "pending_images"
    assert db.query(PushDelivery).count() == 0

    second = client.post(
        f"/api/orders/{order_id}/approve", headers=auth_header(owner_token)
    )
    assert second.json()["status"] == "active"
    delivery = db.query(PushDelivery).one()
    assert delivery.title == "Order goedgekeurd"
    assert delivery.url == f"/?page=receive&order={order_id}"


def test_new_live_ean_order_notifies_courier_once(db, courier_user):
    subscription = _subscription(db, courier_user, "courier-ean")
    from app.models import Organization

    org = Organization(name="EAN Handelaar", slug="push-ean")
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    connection = ChannelConnection(
        organization_id=org.id, channel="shopify", mode="live"
    )
    sku = SKU(
        sku_code="PUSH-EAN-1",
        name="EAN product",
        organization_id=org.id,
        product_type="barcode",
        ean="8712345678906",
    )
    db.add_all([connection, sku])
    db.commit()
    channel_order = NormalizedChannelOrder(
        external_id="push-shopify-1",
        reference="1262",
        customer_name="Webklant",
        financial_status="paid",
        lines=[NormalizedLine(ean=sku.ean, quantity=1)],
    )

    first = import_channel_order(db, connection, channel_order)
    db.commit()
    second = import_channel_order(db, connection, channel_order)
    db.commit()

    assert first.created is True
    assert second.created is False
    deliveries = db.query(PushDelivery).filter_by(subscription_id=subscription.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].title == "Nieuwe EAN-order"
    assert "Shopify-order 1262" in deliveries[0].body


def test_unknown_ean_waits_until_order_becomes_pickable(db, courier_user):
    _subscription(db, courier_user, "courier-blocked-ean")
    from app.models import Organization

    org = Organization(name="EAN Geblokkeerd", slug="push-ean-blocked")
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    connection = ChannelConnection(
        organization_id=org.id, channel="bol", mode="live"
    )
    db.add(connection)
    db.commit()
    channel_order = NormalizedChannelOrder(
        external_id="push-bol-1",
        reference="BOL-42",
        customer_name="bol-klant",
        financial_status="paid",
        lines=[NormalizedLine(ean="8712345678906", quantity=1)],
    )

    blocked = import_channel_order(db, connection, channel_order)
    db.commit()
    assert db.query(PushDelivery).count() == 0

    db.add(
        SKU(
            sku_code="PUSH-EAN-2",
            name="Later gekoppeld",
            organization_id=org.id,
            product_type="barcode",
            ean="8712345678906",
        )
    )
    db.commit()
    ready = import_channel_order(db, connection, channel_order)
    db.commit()

    assert ready.order_id == blocked.order_id
    delivery = db.query(PushDelivery).one()
    assert delivery.title == "Nieuwe EAN-order"
    assert "bol-order BOL-42" in delivery.body


def test_dispatcher_marks_outbox_delivery_sent(db, owner_user, monkeypatch):
    _enable_push(monkeypatch)
    subscription = _subscription(db, owner_user, "dispatch")
    delivery = PushDelivery(
        subscription_id=subscription.id,
        event_key="test:dispatch",
        title="Testmelding",
        body="Testbericht",
        url="/?page=orders",
    )
    db.add(delivery)
    db.commit()
    calls = []
    monkeypatch.setattr(push_notifications, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        push_notifications,
        "webpush",
        lambda **kwargs: calls.append(kwargs),
    )

    assert push_notifications.dispatch_pending_push_deliveries() == 1

    db.expire_all()
    assert db.get(PushDelivery, delivery.id).sent_at is not None
    assert calls[0]["subscription_info"]["endpoint"] == subscription.endpoint


def test_dispatcher_removes_expired_browser_subscription(db, owner_user, monkeypatch):
    _enable_push(monkeypatch)
    subscription = _subscription(db, owner_user, "expired")
    db.add_all(
        [
            PushDelivery(
                subscription_id=subscription.id,
                event_key=f"test:expired:{index}",
                title="Verlopen",
                body="Niet meer afleverbaar",
                url="/",
            )
            for index in range(2)
        ]
    )
    db.commit()
    monkeypatch.setattr(push_notifications, "SessionLocal", TestingSessionLocal)

    def expired(**_kwargs):
        raise WebPushException(
            "Subscription expired", response=SimpleNamespace(status_code=410)
        )

    monkeypatch.setattr(push_notifications, "webpush", expired)

    assert push_notifications.dispatch_pending_push_deliveries() == 0
    db.expire_all()
    assert db.query(PushSubscription).count() == 0
    assert db.query(PushDelivery).count() == 0


def test_transient_failure_uses_backoff_instead_of_immediate_give_up(
    db, owner_user, monkeypatch
):
    _enable_push(monkeypatch)
    subscription = _subscription(db, owner_user, "temporary-failure")
    delivery = PushDelivery(
        subscription_id=subscription.id,
        event_key="test:temporary-failure",
        title="Tijdelijk niet bereikbaar",
        body="Later opnieuw proberen",
        url="/",
    )
    db.add(delivery)
    db.commit()
    calls = []
    monkeypatch.setattr(push_notifications, "SessionLocal", TestingSessionLocal)

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise WebPushException(
            "Provider unavailable", response=SimpleNamespace(status_code=503)
        )

    monkeypatch.setattr(push_notifications, "webpush", unavailable)

    assert push_notifications.dispatch_pending_push_deliveries() == 0
    db.expire_all()
    retried = db.get(PushDelivery, delivery.id)
    assert retried.attempts == 1
    assert retried.failed_at is None
    assert retried.next_attempt_at is not None
    assert retried.next_attempt_at > datetime.datetime.now(datetime.UTC).replace(
        tzinfo=None
    )

    # A second dispatcher tick five seconds later must not burn another attempt;
    # the first retry is scheduled 30 seconds after the failure.
    assert push_notifications.dispatch_pending_push_deliveries() == 0
    assert len(calls) == 1


def test_prune_removes_only_old_terminal_deliveries(db, owner_user, monkeypatch):
    subscription = _subscription(db, owner_user, "prune")
    now = datetime.datetime(2026, 7, 22, 12, 0, 0)
    old = now - datetime.timedelta(days=8)
    recent = now - datetime.timedelta(days=1)
    db.add_all(
        [
            PushDelivery(
                subscription_id=subscription.id,
                event_key="test:old-sent",
                title="Oud",
                body="Verzonden",
                url="/",
                sent_at=old,
            ),
            PushDelivery(
                subscription_id=subscription.id,
                event_key="test:old-failed",
                title="Oud",
                body="Mislukt",
                url="/",
                failed_at=old,
            ),
            PushDelivery(
                subscription_id=subscription.id,
                event_key="test:old-pending",
                title="Oud",
                body="Nog in behandeling",
                url="/",
                created_at=old,
            ),
            PushDelivery(
                subscription_id=subscription.id,
                event_key="test:recent-sent",
                title="Recent",
                body="Verzonden",
                url="/",
                sent_at=recent,
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(push_notifications, "SessionLocal", TestingSessionLocal)

    assert push_notifications.prune_push_deliveries(now) == 2

    db.expire_all()
    remaining = {row.event_key for row in db.query(PushDelivery).all()}
    assert remaining == {"test:old-pending", "test:recent-sent"}
