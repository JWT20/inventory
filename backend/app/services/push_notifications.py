"""Web Push subscriptions, event routing, and transactional outbox delivery."""

import asyncio
import datetime
import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import SessionLocal
from app.models import Order, PushDelivery, PushSubscription, User

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5
DELIVERY_BATCH_SIZE = 50


def _subscriptions_for_users(db: Session, users_query) -> list[PushSubscription]:
    user_ids = [row[0] for row in users_query.with_entities(User.id).all()]
    if not user_ids:
        return []
    return (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id.in_(user_ids))
        .all()
    )


def _enqueue(
    db: Session,
    subscriptions: list[PushSubscription],
    *,
    event_key: str,
    title: str,
    body: str,
    url: str,
) -> int:
    """Add at most one delivery per event/device to the current transaction."""
    if not subscriptions:
        return 0

    subscription_ids = [subscription.id for subscription in subscriptions]
    existing = {
        row[0]
        for row in (
            db.query(PushDelivery.subscription_id)
            .filter(
                PushDelivery.event_key == event_key,
                PushDelivery.subscription_id.in_(subscription_ids),
            )
            .all()
        )
    }
    created = 0
    for subscription in subscriptions:
        if subscription.id in existing:
            continue
        db.add(
            PushDelivery(
                subscription_id=subscription.id,
                event_key=event_key,
                title=title,
                body=body,
                url=url,
            )
        )
        created += 1
    return created


def enqueue_customer_order_created(
    db: Session,
    order: Order,
    *,
    creator: User,
    customer_name: str,
) -> int:
    """Notify this organization's merchants about a customer-created order."""
    if creator.role != "customer":
        return 0
    recipients = db.query(User).filter(
        User.organization_id == order.organization_id,
        User.role.in_(("owner", "member")),
        User.is_active.is_(True),
        User.is_platform_admin.is_(False),
    )
    subscriptions = _subscriptions_for_users(db, recipients)
    return _enqueue(
        db,
        subscriptions,
        event_key=f"order:{order.id}:created",
        title="Nieuwe order",
        body=f"{customer_name} heeft order {order.reference} geplaatst.",
        url=f"/?page=orders&order={order.id}",
    )


def _courier_subscriptions(db: Session) -> list[PushSubscription]:
    recipients = db.query(User).filter(
        User.role == "courier",
        User.is_active.is_(True),
        User.is_platform_admin.is_(False),
    )
    return _subscriptions_for_users(db, recipients)


def enqueue_approved_order_ready(db: Session, order: Order) -> int:
    """Notify the courier once a merchant-approved manual order is pickable."""
    organization_name = order.organization.name if order.organization else "Handelaar"
    return _enqueue(
        db,
        _courier_subscriptions(db),
        event_key=f"order:{order.id}:ready",
        title="Order goedgekeurd",
        body=(
            f"Order {order.reference} van {organization_name} staat klaar om te verwerken."
        ),
        url=f"/?page=receive&order={order.id}",
    )


def enqueue_ean_order_ready(db: Session, order: Order) -> int:
    """Notify the courier once a new or previously blocked EAN order is pickable."""
    organization_name = order.organization.name if order.organization else "Handelaar"
    channel_name = "bol" if order.channel == "bol" else "Shopify"
    reference = order.channel_reference or order.reference
    return _enqueue(
        db,
        _courier_subscriptions(db),
        event_key=f"order:{order.id}:ean-ready",
        title="Nieuwe EAN-order",
        body=(
            f"{channel_name}-order {reference} van {organization_name} "
            "staat klaar om te picken."
        ),
        url=f"/?page=receive&order={order.id}",
    )


def dispatch_pending_push_deliveries(limit: int = DELIVERY_BATCH_SIZE) -> int:
    """Deliver one outbox batch; safe to call repeatedly after process restarts."""
    if not settings.push_enabled:
        return 0

    db = SessionLocal()
    sent = 0
    expired_subscription_ids: set[int] = set()
    try:
        deliveries = (
            db.query(PushDelivery)
            .options(joinedload(PushDelivery.subscription))
            .filter(
                PushDelivery.sent_at.is_(None),
                PushDelivery.failed_at.is_(None),
                PushDelivery.attempts < MAX_DELIVERY_ATTEMPTS,
            )
            .order_by(PushDelivery.created_at, PushDelivery.id)
            .limit(limit)
            .all()
        )
        for delivery in deliveries:
            subscription = delivery.subscription
            if subscription.id in expired_subscription_ids:
                continue
            payload = json.dumps(
                {
                    "title": delivery.title,
                    "body": delivery.body,
                    "url": delivery.url,
                    "tag": delivery.event_key,
                },
                ensure_ascii=False,
            )
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {
                            "p256dh": subscription.p256dh,
                            "auth": subscription.auth,
                        },
                    },
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=3600,
                    timeout=10,
                )
                delivery.sent_at = datetime.datetime.utcnow()
                delivery.last_error = None
                sent += 1
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in (404, 410):
                    # The browser revoked/rotated this endpoint. Deleting the
                    # subscription cascades every now-undeliverable outbox row.
                    expired_subscription_ids.add(subscription.id)
                    db.delete(subscription)
                else:
                    delivery.attempts += 1
                    delivery.last_error = str(exc)[:2000]
                    if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
                        delivery.failed_at = datetime.datetime.utcnow()
                    logger.warning(
                        "Push delivery %s failed (attempt %s): %s",
                        delivery.id,
                        delivery.attempts,
                        exc,
                    )
            except Exception as exc:
                delivery.attempts += 1
                delivery.last_error = str(exc)[:2000]
                if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
                    delivery.failed_at = datetime.datetime.utcnow()
                logger.exception("Unexpected push delivery failure for %s", delivery.id)
            db.commit()
    finally:
        db.close()
    return sent


async def push_dispatch_loop(interval_seconds: int) -> None:
    """Continuously drain the outbox without blocking FastAPI's event loop."""
    logger.info("Web Push dispatcher started (interval=%ss)", interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(dispatch_pending_push_deliveries)
            except Exception:
                logger.exception("Web Push dispatcher iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Web Push dispatcher stopped")
        raise
