"""Unit tests for _call_vision system_instruction support and extract_shipment_document."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services.embedding import (
    EXTRACT_SHIPMENT_USER_PROMPT,
    VisionParseError,
    _call_vision,
    extract_shipment_document,
    parse_classify_and_describe_response,
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


def test_call_vision_without_system_instruction():
    """When no system_instruction is given, generate_content is called without 'config'."""
    mock_response = _make_response('{"is_package": true}')
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        asyncio.run(_call_vision(_make_image(), "test prompt"))

    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    assert "config" not in call_kwargs


def test_call_vision_with_system_instruction_passes_config():
    """When system_instruction is provided, generate_content receives a GenerateContentConfig."""
    from google.genai import types

    mock_response = _make_response('{"result": "ok"}')
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    system_text = "You are a helpful extraction assistant."

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        asyncio.run(_call_vision(_make_image(), "user prompt", system_instruction=system_text))

    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    assert "config" in call_kwargs
    cfg = call_kwargs["config"]
    assert isinstance(cfg, types.GenerateContentConfig)
    assert cfg.system_instruction == system_text


# ---------------------------------------------------------------------------
# _call_vision — transient 5xx ("high demand") retry + model fallback
# ---------------------------------------------------------------------------


def _server_error(code: int):
    from google.genai.errors import ServerError

    return ServerError(code, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})


def test_call_vision_retries_transient_502_then_succeeds():
    """A 502 UNAVAILABLE (ServerError, not ClientError) must be retried, not raised."""
    mock_response = _make_response('{"is_package": true}')
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[_server_error(502), mock_response]
    )

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.asyncio.sleep", new=AsyncMock()), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        result = asyncio.run(_call_vision(_make_image(), "test prompt"))

    assert result == '{"is_package": true}'
    assert mock_client.aio.models.generate_content.call_count == 2


def test_call_vision_falls_back_to_second_model_on_persistent_5xx():
    """When the primary model keeps 5xx-ing, the fallback model is tried."""
    from app.config import settings

    mock_response = _make_response('{"is_package": true}')
    # Primary exhausts all retries (MAX_RETRIES 503s), then fallback succeeds.
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[_server_error(503), _server_error(503), _server_error(503), mock_response]
    )

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.asyncio.sleep", new=AsyncMock()), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        result = asyncio.run(
            _call_vision(_make_image(), "p", model="primary-model", fallback_model="fallback-model")
        )

    assert result == '{"is_package": true}'
    models_used = [c.kwargs["model"] for c in mock_client.aio.models.generate_content.call_args_list]
    assert models_used == ["primary-model", "primary-model", "primary-model", "fallback-model"]


def test_call_vision_does_not_retry_non_retryable_client_error():
    """A 400-class error must propagate immediately without retry/fallback."""
    from google.genai.errors import ClientError

    err = ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}})
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(side_effect=err)

    with patch("app.services.embedding._get_client", return_value=mock_client), \
         patch("app.services.embedding._get_semaphore", return_value=asyncio.Semaphore(1)), \
         patch("app.services.embedding.asyncio.sleep", new=AsyncMock()), \
         patch("app.services.embedding.get_langfuse_client", side_effect=Exception("no langfuse")):
        with pytest.raises(ClientError):
            asyncio.run(_call_vision(_make_image(), "p", fallback_model=""))

    assert mock_client.aio.models.generate_content.call_count == 1


# ---------------------------------------------------------------------------
# parse_classify_and_describe_response — hard-fail instead of garbage fallback
# ---------------------------------------------------------------------------


def test_parse_classify_and_describe_valid():
    is_package, description = parse_classify_and_describe_response(
        '{"is_package": true, "description": "Red wine box, Casa Santos Lima"}'
    )
    assert is_package is True
    assert description == "Red wine box, Casa Santos Lima"


def test_parse_classify_and_describe_raises_on_truncated_json():
    """The exact production corruption: a truncated, unterminated envelope.

    The old code stored this as ``text[:50]`` and embedded it; now it must
    raise so nothing gets embedded.
    """
    raw = '{"is_package": true, "description": "Brown cardboa'
    with pytest.raises(VisionParseError):
        parse_classify_and_describe_response(raw)


def test_parse_classify_and_describe_raises_on_missing_key():
    with pytest.raises(VisionParseError):
        parse_classify_and_describe_response('{"foo": "bar"}')


@pytest.mark.parametrize(
    "raw",
    [
        '{"is_package": true}',
        '{"is_package": true, "description": null}',
        '{"is_package": true, "description": ""}',
        '{"is_package": true, "description": "   "}',
        '{"is_package": true, "description": "\\"   \\""}',
    ],
)
def test_parse_classify_and_describe_raises_on_missing_package_description(raw):
    with pytest.raises(VisionParseError):
        parse_classify_and_describe_response(raw)


def test_parse_classify_and_describe_allows_empty_description_for_non_package():
    is_package, description = parse_classify_and_describe_response(
        '{"is_package": false}'
    )
    assert is_package is False
    assert description == ""


# ---------------------------------------------------------------------------
# extract_shipment_document — system_instruction and user prompt
# ---------------------------------------------------------------------------


def test_extract_shipment_document_passes_system_instruction():
    """extract_shipment_document must pass a system_instruction to _call_vision."""
    payload = json.dumps({
        "supplier_name": "Anfors",
        "reference": "PKB-001",
        "document_type": "pakbon",
        "raw_text": "sample",
        "lines": [],
    })

    with patch("app.services.embedding.asyncio.to_thread", new=AsyncMock(return_value=_make_image())), \
         patch("app.services.embedding.get_prompt_required", return_value="system prompt text"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)) as mock_cv:
        asyncio.run(extract_shipment_document(b"fake-image-bytes"))

    mock_cv.assert_called_once()
    call_kwargs = mock_cv.call_args.kwargs
    assert call_kwargs.get("system_instruction") == "system prompt text"


def test_extract_shipment_document_uses_user_prompt():
    """extract_shipment_document must pass EXTRACT_SHIPMENT_USER_PROMPT as the user prompt."""
    payload = json.dumps({
        "supplier_name": "",
        "reference": "",
        "document_type": "unknown",
        "raw_text": "",
        "lines": [],
    })

    with patch("app.services.embedding.asyncio.to_thread", new=AsyncMock(return_value=_make_image())), \
         patch("app.services.embedding.get_prompt_required", return_value="system prompt text"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)) as mock_cv:
        asyncio.run(extract_shipment_document(b"fake-image-bytes"))

    mock_cv.assert_called_once()
    positional_args = mock_cv.call_args.args
    # Second positional arg is the user prompt
    assert positional_args[1] == EXTRACT_SHIPMENT_USER_PROMPT


# ---------------------------------------------------------------------------
# extract_shipment_document — None normalization for string fields
# ---------------------------------------------------------------------------


def test_extract_shipment_document_normalizes_null_string_fields():
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
         patch("app.services.embedding.get_prompt_required", return_value="sys"), \
         patch("app.services.embedding._call_vision", new=AsyncMock(return_value=payload)):
        result = asyncio.run(extract_shipment_document(b"fake-image-bytes"))

    assert result["supplier_name"] == ""
    assert result["reference"] == ""
    assert result["lines"][0]["supplier_code"] == ""
    assert result["lines"][0]["description"] == ""
