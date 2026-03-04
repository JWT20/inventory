"""Kafka event publisher for business event logging.

Publishes structured events to the 'warehouse_events' topic.
Gracefully degrades — if Kafka is unavailable, logs a warning and continues.
"""

import json
import logging
import time
import uuid

from app.config import settings

logger = logging.getLogger(__name__)

_producer = None


def init_producer():
    """Initialize the Kafka producer. Call on app startup."""
    global _producer
    if not settings.kafka_bootstrap_servers:
        logger.info("KAFKA_BOOTSTRAP_SERVERS not set — event publishing disabled")
        return

    try:
        from confluent_kafka import Producer

        _producer = Producer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "linger.ms": 100,
            "batch.num.messages": 50,
        })
        logger.info("Kafka producer initialized (%s)", settings.kafka_bootstrap_servers)
    except Exception:
        logger.warning("Failed to initialize Kafka producer", exc_info=True)
        _producer = None


def shutdown_producer():
    """Flush and close the Kafka producer. Call on app shutdown."""
    global _producer
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
        except Exception:
            logger.warning("Error flushing Kafka producer", exc_info=True)
        _producer = None
        logger.info("Kafka producer shut down")


def _delivery_report(err, msg):
    if err is not None:
        logger.warning("Kafka delivery failed: %s", err)


def publish_event(
    event_type: str,
    *,
    details: dict | None = None,
    user=None,
    resource_type: str = "",
    resource_id: int | None = None,
):
    """Publish a business event to Kafka.

    Non-blocking — events are buffered and sent asynchronously.
    If Kafka is unavailable, logs a warning and returns silently.
    """
    if _producer is None:
        return

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
        "user_id": user.id if user else 0,
        "username": user.username if user else "",
        "resource_type": resource_type,
        "resource_id": resource_id or 0,
        "details": details or {},
    }

    try:
        _producer.produce(
            "warehouse_events",
            key=event_type,
            value=json.dumps(event).encode("utf-8"),
            callback=_delivery_report,
        )
        _producer.poll(0)
    except Exception:
        logger.warning("Failed to publish event %s", event_type, exc_info=True)
