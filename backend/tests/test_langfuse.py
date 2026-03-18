"""Tests for Langfuse integration — graceful degradation when not configured."""

import os
from unittest.mock import patch

import pytest


def test_langfuse_disabled_when_no_keys():
    """Langfuse client returns None when keys are not configured."""
    # Reset module state
    import app.services.langfuse_client as lf
    lf._langfuse = None
    lf._initialized = False

    with patch.object(lf.settings, "langfuse_public_key", ""), \
         patch.object(lf.settings, "langfuse_secret_key", ""):
        result = lf.get_langfuse()
        assert result is None


def test_langfuse_score_noop_when_disabled():
    """score_trace does nothing when Langfuse is disabled."""
    import app.services.langfuse_client as lf
    lf._langfuse = None
    lf._initialized = False

    with patch.object(lf.settings, "langfuse_public_key", ""), \
         patch.object(lf.settings, "langfuse_secret_key", ""):
        # Should not raise
        lf.score_trace("fake-trace-id", "test_score", 0.95)


def test_langfuse_shutdown_when_not_initialized():
    """shutdown_langfuse works even when client was never initialized."""
    import app.services.langfuse_client as lf
    lf._langfuse = None
    lf._initialized = False

    # Should not raise
    lf.shutdown_langfuse()
    assert lf._initialized is False
