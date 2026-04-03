"""Unit tests for _call_vision system_instruction support and extract_shipment_document."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services.embedding import (
    EXTRACT_SHIPMENT_USER_PROMPT,
    _call_vision,
    extract_shipment_document,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image() -> Image.Image:
    """Return a tiny RGB image for use in tests."""
    return Image.new("RGB", (64, 64), color=(128, 128, 128))


def _make_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# _call_vision — system_instruction forwarded to GenerateContentConfig
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_vision_without_system_instruction():
    """When no system_instruction is given, generate_content is called without 'config'."""
    mock_response = _make_response('{"is_package": true}')
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        await _call_vision(_make_image(), "test prompt")

    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    assert "config" not in call_kwargs


@pytest.mark.asyncio
async def test_call_vision_with_system_instruction_passes_config():
    """When system_instruction is provided, generate_content receives a GenerateContentConfig."""
    from google.genai import types

    mock_response = _make_response('{"result": "ok"}')
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    system_text = "You are a helpful extraction assistant."

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        await _call_vision(_make_image(), "user prompt", system_instruction=system_text)

    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    assert "config" in call_kwargs
    cfg = call_kwargs["config"]
    assert isinstance(cfg, types.GenerateContentConfig)
    assert cfg.system_instruction == system_text


# ---------------------------------------------------------------------------
# extract_shipment_document — system_instruction and user prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_shipment_document_passes_system_instruction():
    """extract_shipment_document must pass a system_instruction to _call_vision."""
    payload = json.dumps({
        "supplier_name": "Anfors",
        "reference": "PKB-001",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [],
    })

    with patch("app.services.embedding.asyncio.to_thread", new=AsyncMock(return_value=_make_image())), \
         patch("app.services.embedding.get_prompt", return_value="system prompt text"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)) as mock_cv:
        result = await extract_shipment_document(b"fake-image-bytes")

    mock_cv.assert_called_once()
    call_kwargs = mock_cv.call_args.kwargs
    assert call_kwargs.get("system_instruction") == "system prompt text"


@pytest.mark.asyncio
async def test_extract_shipment_document_uses_user_prompt():
    """extract_shipment_document must pass EXTRACT_SHIPMENT_USER_PROMPT as the user prompt."""
    payload = json.dumps({
        "supplier_name": "",
        "reference": "",
        "document_type": "unknown",
        "raw_text": "",
        "lines": [],
    })

    with patch("app.services.embedding.asyncio.to_thread", new=AsyncMock(return_value=_make_image())), \
         patch("app.services.embedding.get_prompt", return_value="system prompt text"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)) as mock_cv:
        await extract_shipment_document(b"fake-image-bytes")

    mock_cv.assert_called_once()
    positional_args = mock_cv.call_args.args
    # Second positional arg is the user prompt
    assert positional_args[1] == EXTRACT_SHIPMENT_USER_PROMPT


# ---------------------------------------------------------------------------
# extract_shipment_document — None normalization for string fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_shipment_document_normalizes_null_string_fields():
    """None values in supplier_code/description of lines must be coerced to empty string."""
    payload = json.dumps({
        "supplier_name": None,
        "reference": None,
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [
            {
                "supplier_code": None,
                "description": None,
                "quantity_boxes": 3,
                "confidence": 0.7,
            }
        ],
    })

    with patch("app.services.embedding.asyncio.to_thread", new=AsyncMock(return_value=_make_image())), \
         patch("app.services.embedding.get_prompt", return_value="sys"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)):
        result = await extract_shipment_document(b"fake-image-bytes")

    assert result["supplier_name"] == ""
    assert result["reference"] == ""
    assert result["lines"][0]["supplier_code"] == ""
    assert result["lines"][0]["description"] == ""
