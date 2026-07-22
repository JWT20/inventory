"""Web Push subscriptions, event routing, and transactional outbox delivery."""

import asyncio
import datetime
import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import SessionLocal
from app.models import Order, PushDelivery, PushSubscription, User

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 10
DELIVERY_BATCH_SIZE = 50
DELIVERY_RETENTION_DAYS = 7
PRUNE_INTERVAL_SECONDS = 60 * 60
# A delivery that keeps failing remains retryable for roughly eight hours. The
# first retry is quick; later retries stop hammering a provider during an outage.
RETRY_DELAYS_SECONDS = (30, 60, 120, 300, 600, 1800, 3600, 7200, 14400)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _schedule_retry(
    delivery: PushDelivery, exc: Exception, now: datetime.datetime
) -> None:
    delivery.attempts += 1
    delivery.last_error = str(exc)[:2000]
    if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
        delivery.failed_at = now
        delivery.next_attempt_at = None
        return
    delay_seconds = RETRY_DELAYS_SECONDS[
        min(delivery.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
    ]
    delivery.next_attempt_at = now + datetime.timedelta(seconds=delay_seconds)


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
        now = _utcnow()
        deliveries = (
            db.query(PushDelivery)
            .options(joinedload(PushDelivery.subscription))
            .filter(
                PushDelivery.sent_at.is_(None),
                PushDelivery.failed_at.is_(None),
                PushDelivery.attempts < MAX_DELIVERY_ATTEMPTS,
                or_(
                    PushDelivery.next_attempt_at.is_(None),
                    PushDelivery.next_attempt_at <= now,
                ),
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
                delivery.sent_at = now
                delivery.last_error = None
                delivery.next_attempt_at = None
                sent += 1
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in (404, 410):
                    # The browser revoked/rotated this endpoint. Deleting the
                    # subscription cascades every now-undeliverable outbox row.
                    expired_subscription_ids.add(subscription.id)
                    db.delete(subscription)
                else:
                    _schedule_retry(delivery, exc, now)
                    logger.warning(
                        "Push delivery %s failed (attempt %s, retry at %s): %s",
                        delivery.id,
                        delivery.attempts,
                        delivery.next_attempt_at,
                        exc,
                    )
            except Exception as exc:
                _schedule_retry(delivery, exc, now)
                logger.exception("Unexpected push delivery failure for %s", delivery.id)
            db.commit()
    finally:
        db.close()
    return sent


def prune_push_deliveries(now: datetime.datetime | None = None) -> int:
    """Delete terminal outbox rows after the retention window."""
    db = SessionLocal()
    try:
        cutoff = (now or _utcnow()) - datetime.timedelta(days=DELIVERY_RETENTION_DAYS)
        deleted = (
            db.query(PushDelivery)
            .filter(
                or_(
                    PushDelivery.sent_at < cutoff,
                    PushDelivery.failed_at < cutoff,
                )
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Pruned %s old Web Push outbox rows", deleted)
        return deleted
    finally:
        db.close()


async def push_dispatch_loop(interval_seconds: int) -> None:
    """Continuously drain the outbox without blocking FastAPI's event loop.

    IMPORTANT: like ``autosync_loop``, this currently assumes one API process.
    The outbox has no cross-process claim/lease; add one (for example a claimed
    state or one-row ``FOR UPDATE SKIP LOCKED`` transaction) before running
    multiple backend replicas, otherwise two dispatchers can send the same row.
    """
    logger.info("Web Push dispatcher started (interval=%ss)", interval_seconds)
    last_prune_at: datetime.datetime | None = None
    try:
        while True:
            try:
                await asyncio.to_thread(dispatch_pending_push_deliveries)
                now = _utcnow()
                if (
                    last_prune_at is None
                    or (now - last_prune_at).total_seconds() >= PRUNE_INTERVAL_SECONDS
                ):
                    await asyncio.to_thread(prune_push_deliveries, now)
                    last_prune_at = now
            except Exception:
                logger.exception("Web Push dispatcher iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Web Push dispatcher stopped")
        raise
