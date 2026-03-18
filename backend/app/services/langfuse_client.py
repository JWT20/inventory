"""Langfuse client for LLM observability.

Gracefully degrades — if Langfuse keys are not configured, all tracing
is silently skipped (same pattern as Kafka in events.py).
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_langfuse = None
_initialized = False


def get_langfuse():
    """Return the Langfuse client, or None if not configured."""
    global _langfuse, _initialized

    if _initialized:
        return _langfuse

    _initialized = True

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.info("Langfuse not configured (no keys) — LLM tracing disabled")
        return None

    try:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse client initialized (%s)", settings.langfuse_host)
        return _langfuse
    except Exception:
        logger.warning("Failed to initialize Langfuse client", exc_info=True)
        return None


def shutdown_langfuse():
    """Flush and shutdown Langfuse. Call on app shutdown."""
    global _langfuse, _initialized
    if _langfuse is not None:
        try:
            _langfuse.flush()
        except Exception:
            logger.warning("Error flushing Langfuse", exc_info=True)
        _langfuse = None
    _initialized = False
    logger.info("Langfuse client shut down")


def score_trace(trace_id: str, name: str, value: float, comment: str | None = None):
    """Record a score on a Langfuse trace. No-op if Langfuse is disabled."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.create_score(trace_id=trace_id, name=name, value=value, comment=comment)
    except Exception:
        logger.warning("Failed to record Langfuse score %s", name, exc_info=True)
