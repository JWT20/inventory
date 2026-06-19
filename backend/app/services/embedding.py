import asyncio
import logging
import re
import time

import base64

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
import io

# Teach Pillow to decode HEIC/HEIF (the default iPhone camera format). Without
# this, Image.open() raises UnidentifiedImageError on Apple photos.
register_heif_opener()

from langfuse import observe, get_client as get_langfuse_client

from app.config import settings
from app.services.langfuse_client import get_prompt_required
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)


class VisionParseError(ValueError):
    """Raised when a vision response cannot be parsed into a usable result.

    Previously the parser silently fell back to ``text[:50]``, which embedded a
    truncated raw-JSON snippet (``{"is_package": true, "description": "...``) as
    if it were a real description. Those garbage embeddings clustered together
    and produced false "looks like" matches between unrelated products. We now
    fail loudly instead: reference-image processing marks the image ``failed``
    and scan endpoints return a 502, so nothing bad is ever embedded.
    """


MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds
MAX_VISION_DIMENSION = 1024  # px – downscale before sending to Gemini
MAX_UPLOAD_DIMENSION = 2048  # px – cap stored reference images (bounds file size)

_client: genai.Client | None = None

# Semaphore to limit concurrent Gemini API requests (prevents quota exhaustion
# and thread-pool starvation under load).
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return the concurrency-limiting semaphore, creating it lazily.

    Must be called from within a running event loop.
    """
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.gemini_max_concurrent)
    return _semaphore


# NOTE: All LLM prompts live in Langfuse only (fetched via get_prompt_required;
# see docs/flessen.md, sectie I). There are intentionally no code fallbacks:
# without a configured Langfuse these calls fail loudly.


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = "\n".join(text.split("\n")[:-1])
    return text.strip()


def parse_classify_response(raw: str) -> tuple[bool, str]:
    """Parse the classification response.

    Returns (is_package, summary).
    """
    import json as _json

    text = _strip_markdown_fences(raw)

    try:
        data = _json.loads(text)
        if isinstance(data, dict) and "is_package" in data:
            is_package = bool(data["is_package"])
            summary = str(data.get("summary", "")).strip()
            return is_package, summary
    except (_json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallback: look for keywords suggesting packaging
    logger.warning("Classification response not valid JSON, using heuristic: %s", text[:100])
    lower = text.lower()
    package_words = {"box", "case", "crate", "carton", "package", "packaging", "parcel"}
    has_package_word = any(w in lower for w in package_words)
    return has_package_word, text[:50]


def parse_classify_and_describe_response(raw: str) -> tuple[bool, str]:
    """Parse the combined classify+describe response.

    Returns (is_package, description_or_summary).
    """
    import json as _json

    text = _strip_markdown_fences(raw)

    try:
        data = _json.loads(text)
    except (_json.JSONDecodeError, TypeError, ValueError) as exc:
        # No silent fallback: the old ``text[:50]`` path embedded a truncated raw
        # ``{"is_package": true, "description": "...`` snippet as if it were a real
        # description. Fail loudly and log the full raw response so the cause
        # (truncation, safety block, malformed JSON) is visible — the matching
        # finish_reason/usage is logged by _call_vision for the same request.
        logger.error(
            "Vision classify+describe response is not valid JSON (len=%d): %r",
            len(text),
            text[:2000],
        )
        raise VisionParseError(
            "classify-and-describe response was not valid JSON"
        ) from exc

    if not (isinstance(data, dict) and "is_package" in data):
        logger.error(
            "Vision classify+describe response JSON missing 'is_package' key: %r",
            text[:2000],
        )
        raise VisionParseError(
            "classify-and-describe response missing 'is_package' key"
        )

    is_package = bool(data["is_package"])
    description_value = data.get("description")
    description = "" if description_value is None else str(description_value).strip()
    if description.startswith('"') and description.endswith('"'):
        description = description[1:-1].strip()
    if is_package and not description:
        logger.error(
            "Vision classify+describe response missing usable package description: %r",
            text[:2000],
        )
        raise VisionParseError(
            "classify-and-describe response missing usable package description"
        )
    return is_package, description


def _get_client() -> genai.Client:
    """Shared Gemini API client (default v1beta endpoint)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


class UnsupportedImageError(ValueError):
    """Raised when uploaded bytes cannot be decoded as an image."""


def normalize_upload_to_jpeg(
    image_bytes: bytes, max_dimension: int = MAX_UPLOAD_DIMENSION
) -> bytes:
    """Decode arbitrary uploaded image bytes and re-encode as upright JPEG.

    Handles HEIC/HEIF (iPhone) and other Pillow-supported formats, bakes in
    EXIF orientation, downscales to ``max_dimension`` on the longest side, and
    guarantees the stored bytes are a real JPEG so the ``.jpg`` storage key is
    honest and downstream decoding always succeeds. The downscale also keeps
    the output size bounded (a HEIC can expand on decode).

    Raises ``UnsupportedImageError`` if the bytes are not a decodable image.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedImageError(str(exc)) from exc
    image = _optimize_pil_for_vision(image, max_dimension)  # exif_transpose + downscale
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _optimize_pil_for_vision(image: Image.Image, max_dimension: int | None = None) -> Image.Image:
    """Downscale a PIL image so its longest side is at most ``max_dimension`` px."""
    limit = max_dimension or MAX_VISION_DIMENSION
    image = ImageOps.exif_transpose(image)
    w, h = image.size
    if max(w, h) > limit:
        scale = limit / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)
        logger.info("Resized image from %dx%d to %dx%d for vision (limit=%d)", w, h, new_w, new_h, limit)
    return image


def optimize_for_vision(image_bytes: bytes, max_dimension: int | None = None) -> Image.Image:
    """Downscale image so its longest side is at most ``max_dimension`` px.

    Defaults to ``MAX_VISION_DIMENSION`` (suitable for box classification).
    Document extraction passes a higher limit so small table digits stay legible.
    """
    return _optimize_pil_for_vision(Image.open(io.BytesIO(image_bytes)), max_dimension)


def _usage_details_from_gemini(response) -> dict[str, int]:
    """Map a Gemini ``usage_metadata`` block to Langfuse ``usage_details``.

    Returns an empty dict when the response carries no usage metadata (e.g.
    embedding responses), so callers can pass ``usage_details=... or None``.

    Note: ``total_token_count`` is intentionally omitted. Langfuse computes cost
    per usage-key, so sending ``total`` alongside ``input``/``output`` risks
    double-counting if a ``total`` price is defined.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}

    details: dict[str, int] = {}

    if getattr(usage, "prompt_token_count", None) is not None:
        details["input"] = usage.prompt_token_count

    if getattr(usage, "candidates_token_count", None) is not None:
        details["output"] = usage.candidates_token_count

    # Only useful if the Langfuse model price definition prices these usage types.
    if getattr(usage, "cached_content_token_count", None) is not None:
        details["cached_input"] = usage.cached_content_token_count

    if getattr(usage, "thoughts_token_count", None) is not None:
        details["reasoning"] = usage.thoughts_token_count

    return details


def _finish_reason(response) -> str | None:
    """Best-effort extraction of the first candidate's finish reason.

    ``MAX_TOKENS`` means the output was truncated (raise the token cap or the
    model is looping); ``SAFETY``/``RECITATION`` mean it was blocked. Logged on
    parse failures so the cause is visible without re-running the call.
    """
    try:
        return str(response.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return None


@observe(as_type="generation")
async def _call_vision(
    image: Image.Image,
    prompt: str,
    *,
    model: str | None = None,
    system_instruction: str | None = None,
) -> str:
    """Call Gemini Vision asynchronously with retry logic. Returns raw response text."""
    model = model or settings.gemini_vision_model
    client = _get_client()
    logger.info("Calling Gemini Vision model=%s", model)
    t0 = time.perf_counter()

    generate_kwargs: dict = {
        "model": model,
        "contents": [prompt, image],
    }
    if system_instruction:
        generate_kwargs["config"] = types.GenerateContentConfig(
            system_instruction=system_instruction,
        )

    async with _get_semaphore():
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.aio.models.generate_content(**generate_kwargs)
                break
            except ClientError as e:
                if e.code == 429 and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * attempt
                    logger.warning("Gemini rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                else:
                    logger.exception("Gemini Vision API call failed (model=%s, attempt=%d)", model, attempt)
                    raise

    vision_ms = (time.perf_counter() - t0) * 1000
    finish_reason = _finish_reason(response)
    logger.info("[TIMING] gemini_vision=%.0fms finish_reason=%s", vision_ms, finish_reason)
    # A non-STOP finish (MAX_TOKENS/SAFETY/RECITATION) means the text is partial
    # or blocked — the usual root cause behind an unparseable response below.
    if finish_reason is not None and "STOP" not in finish_reason:
        logger.warning(
            "Gemini Vision finished abnormally: finish_reason=%s usage=%s",
            finish_reason,
            _usage_details_from_gemini(response) or "{}",
        )

    try:
        langfuse = get_langfuse_client()
        # Include the image as base64 so Langfuse can display it in the trace
        buf = io.BytesIO()
        image.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        langfuse_input = []
        if system_instruction:
            langfuse_input.append({"role": "system", "content": system_instruction})
        langfuse_input.append(
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]},
        )
        langfuse.update_current_generation(
            model=model,
            input=langfuse_input,
            output=response.text,
            usage_details=_usage_details_from_gemini(response) or None,
        )
    except Exception:
        pass  # Langfuse not initialized or not in traced context

    return response.text


@observe(as_type="generation")
async def _call_text(
    prompt: str,
    *,
    model: str | None = None,
    system_instruction: str | None = None,
) -> str:
    """Call Gemini with text-only input (no image). Returns raw response text."""
    model = model or settings.gemini_vision_model
    client = _get_client()
    logger.info("Calling Gemini text model=%s", model)
    t0 = time.perf_counter()

    generate_kwargs: dict = {"model": model, "contents": [prompt]}
    if system_instruction:
        generate_kwargs["config"] = types.GenerateContentConfig(
            system_instruction=system_instruction,
        )

    async with _get_semaphore():
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.aio.models.generate_content(**generate_kwargs)
                break
            except ClientError as e:
                if e.code == 429 and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * attempt
                    logger.warning("Gemini rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                else:
                    logger.exception("Gemini text API call failed (model=%s, attempt=%d)", model, attempt)
                    raise

    logger.info("[TIMING] gemini_text=%.0fms", (time.perf_counter() - t0) * 1000)

    try:
        langfuse = get_langfuse_client()
        langfuse_input = []
        if system_instruction:
            langfuse_input.append({"role": "system", "content": system_instruction})
        langfuse_input.append({"role": "user", "content": prompt})
        langfuse.update_current_generation(
            model=model,
            input=langfuse_input,
            output=response.text,
            usage_details=_usage_details_from_gemini(response) or None,
        )
    except Exception:
        pass  # Langfuse not initialized or not in traced context

    return response.text


@observe()
async def classify_image(image_bytes: bytes) -> tuple[bool, str]:
    """Step 1: Classify whether the image shows a box/package.

    Returns (is_package, summary).
    """
    t0 = time.perf_counter()
    image = await asyncio.to_thread(optimize_for_vision, image_bytes)
    resize_ms = (time.perf_counter() - t0) * 1000
    logger.info("[TIMING] image_resize=%.0fms", resize_ms)

    prompt = get_prompt_required("classify")
    raw_text = await _call_vision(image, prompt)
    logger.info("Classification raw response: %s", raw_text[:120])

    is_package, summary = parse_classify_response(raw_text)
    logger.info("Classification result: is_package=%s, summary: %s", is_package, summary)
    return is_package, summary


@observe()
async def describe_package(image_bytes: bytes) -> str:
    """Step 2: Describe the packaging for embedding.

    Returns a description optimized for text-similarity search.
    Always call this AFTER classify_image confirms it's a package,
    or when the user has overridden classification.
    """
    image = await asyncio.to_thread(optimize_for_vision, image_bytes)
    prompt = get_prompt_required("describe-package")
    raw_text = await _call_vision(image, prompt)
    logger.info("Description raw response: %s", raw_text[:120])

    description = _strip_markdown_fences(raw_text).strip()
    # If the response is wrapped in quotes, strip them
    if description.startswith('"') and description.endswith('"'):
        description = description[1:-1]

    logger.info("Package description: %s", description[:100])
    return description


async def describe_image(image_bytes: bytes) -> tuple[str, bool]:
    """Classify and describe in one call. Kept for backward compatibility.

    Returns (description, is_package).
    """
    is_package, description = await classify_and_describe(image_bytes)
    return description, is_package


@observe(as_type="span")
async def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using gemini-embedding-001."""
    client = _get_client()

    logger.info("Calling Gemini Embedding model=%s", settings.gemini_embedding_model)
    t0 = time.perf_counter()

    async with _get_semaphore():
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await client.aio.models.embed_content(
                    model=settings.gemini_embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
                )
                break
            except ClientError as e:
                if e.code == 429 and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * attempt
                    logger.warning("Gemini rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                else:
                    logger.exception("Gemini Embedding API call failed (model=%s, attempt=%d)", settings.gemini_embedding_model, attempt)
                    raise

    embedding_ms = (time.perf_counter() - t0) * 1000

    logger.info("[TIMING] gemini_embedding=%.0fms", embedding_ms)

    try:
        langfuse = get_langfuse_client()
        # Kept as a "generation" observation so Langfuse reliably prices it.
        # Gemini embedding responses usually carry no usage_metadata, so this is
        # often empty (embedding cost then stays zero unless you add your own
        # token estimate). We pass it when present rather than guessing.
        langfuse.update_current_generation(
            model=settings.gemini_embedding_model,
            input=text,
            metadata={"output_dimensionality": EMBEDDING_DIM},
            usage_details=_usage_details_from_gemini(result) or None,
        )
    except Exception:
        pass  # Langfuse not initialized or not in traced context

    return result.embeddings[0].values


def assess_description_quality(description: str) -> str:
    """Assess the quality of a description for embedding purposes.

    Returns "high", "medium", or "low".
    """
    words = description.split()
    word_count = len(words)

    # Count words that look like transcribed text (capitalized, numbers, brand-like)
    transcribed = sum(1 for w in words if re.search(r'[A-Z]{2,}', w) or re.search(r'\d{4}', w))

    if word_count < 10:
        return "low"
    if transcribed >= 3 and word_count >= 20:
        return "high"
    if transcribed >= 1 and word_count >= 15:
        return "medium"
    return "low"


@observe()
async def describe_and_embed(image_bytes: bytes) -> tuple[str, list[float], str]:
    """Skip classification, go straight to describe + embed.

    Used when the user has overridden classification (skip_wine_check=True).
    Returns (description, embedding, quality).
    """
    t_start = time.perf_counter()
    logger.info("Processing overridden image (%d bytes) — skipping classification", len(image_bytes))

    description = await describe_package(image_bytes)
    quality = assess_description_quality(description)
    embedding = await generate_embedding(description)

    total_ms = (time.perf_counter() - t_start) * 1000
    logger.info("[TIMING] describe_and_embed_total=%.0fms quality=%s", total_ms, quality)
    return description, embedding, quality


@observe()
async def classify_and_describe(image_bytes: bytes) -> tuple[bool, str]:
    """Classify and describe in a single Gemini call.

    Returns (is_package, description_or_summary).
    """
    t0 = time.perf_counter()
    image = await asyncio.to_thread(optimize_for_vision, image_bytes)
    resize_ms = (time.perf_counter() - t0) * 1000
    logger.info("[TIMING] image_resize=%.0fms", resize_ms)

    prompt = get_prompt_required("classify-and-describe")
    raw_text = await _call_vision(image, prompt)
    logger.info("Classify+describe raw response: %s", raw_text[:200])

    is_package, description = parse_classify_and_describe_response(raw_text)
    logger.info("Result: is_package=%s, description: %s", is_package, description[:100])
    return is_package, description


@observe()
async def process_image(image_bytes: bytes) -> tuple[str, list[float] | None, bool]:
    """Full pipeline: classify + describe (single call) → embed.

    Returns (description, embedding, is_package).
    If the image is not a package, embedding is None (skipped to save cost).
    """
    t_start = time.perf_counter()
    logger.info("Processing image (%d bytes)", len(image_bytes))

    is_package, description = await classify_and_describe(image_bytes)

    if not is_package:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info("[TIMING] process_image_total=%.0fms (rejected: not a package — %s)", total_ms, description)
        return description, None, False

    embedding = await generate_embedding(description)
    total_ms = (time.perf_counter() - t_start) * 1000
    logger.info("[TIMING] process_image_total=%.0fms", total_ms)
    return description, embedding, True


EXTRACT_SHIPMENT_USER_PROMPT = "\n".join([
    "Return ONLY JSON matching the schema.",
    "Do not omit fields; use empty string for missing string fields and [] for missing arrays.",
])


def _is_pdf(data: bytes) -> bool:
    """Detect a PDF by its magic bytes (allowing a small leading offset)."""
    return b"%PDF-" in data[:1024]


def pdf_to_images(pdf_bytes: bytes) -> list[Image.Image]:
    """Render every page of a PDF to a PIL image suitable for vision.

    Uses a higher render scale so small table digits stay legible; the vision
    optimizer downsizes afterwards if needed.
    """
    import fitz  # PyMuPDF

    images: list[Image.Image] = []
    # 2x zoom (~144 DPI) keeps packing-slip tables readable without huge payloads.
    matrix = fitz.Matrix(2, 2)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    return images


def _parse_shipment_json(raw_text: str) -> dict:
    """Parse the LLM JSON response into the normalized shipment dict."""
    cleaned = _strip_markdown_fences(raw_text)
    import json as _json
    try:
        parsed = _json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("Parsed payload is not an object")
        parsed.setdefault("supplier_name", "")
        parsed.setdefault("reference", "")
        parsed.setdefault("document_type", "unknown")
        parsed.setdefault("raw_text", cleaned[:500])
        parsed.setdefault("lines", [])
        # Normalize None to "" for top-level string fields so callers never see "None"
        for _str_field in ("supplier_name", "reference", "document_type", "raw_text"):
            if parsed.get(_str_field) is None:
                parsed[_str_field] = ""
        if not isinstance(parsed["lines"], list):
            parsed["lines"] = []
        # Normalize None → "" for string fields within each line
        for line in parsed["lines"]:
            if isinstance(line, dict):
                for field_name in ("supplier_code", "description"):
                    if line.get(field_name) is None:
                        line[field_name] = ""
        return parsed
    except Exception:
        logger.warning("Shipment extraction not valid JSON; returning empty fallback")
        return {
            "supplier_name": "",
            "reference": "",
            "document_type": "unknown",
            "raw_text": cleaned[:1000],
            "lines": [],
        }


async def _extract_shipment_from_image(image: Image.Image, system_prompt: str) -> dict:
    raw_text = await _call_vision(
        image,
        EXTRACT_SHIPMENT_USER_PROMPT,
        model=settings.gemini_extraction_model,
        system_instruction=system_prompt,
    )
    return _parse_shipment_json(raw_text)


def _merge_shipment_results(pages: list[dict]) -> dict:
    """Combine per-page extraction dicts: concatenate lines, take first non-empty header fields."""
    merged: dict = {
        "supplier_name": "",
        "reference": "",
        "document_type": "unknown",
        "raw_text": "",
        "lines": [],
    }
    raw_chunks: list[str] = []
    for page in pages:
        for field in ("supplier_name", "reference"):
            if not merged[field] and page.get(field):
                merged[field] = page[field]
        page_type = page.get("document_type") or "unknown"
        if merged["document_type"] == "unknown" and page_type != "unknown":
            merged["document_type"] = page_type
        if page.get("raw_text"):
            raw_chunks.append(str(page["raw_text"]))
        if isinstance(page.get("lines"), list):
            merged["lines"].extend(page["lines"])
    merged["raw_text"] = "\n".join(raw_chunks)[:2000]
    return merged


@observe()
async def extract_shipment_document(file_bytes: bytes) -> dict:
    """Extract structured shipment data from a pakbon/factuur image or PDF.

    PDFs are rendered to one image per page; lines from all pages are merged.
    """
    system_prompt = get_prompt_required("extract-shipment-document")

    if _is_pdf(file_bytes):
        pages = await asyncio.to_thread(pdf_to_images, file_bytes)
        if not pages:
            return _parse_shipment_json("")
        optimized = [
            await asyncio.to_thread(
                _optimize_pil_for_vision, page, settings.gemini_extraction_max_dimension
            )
            for page in pages
        ]
        results = await asyncio.gather(
            *(_extract_shipment_from_image(img, system_prompt) for img in optimized)
        )
        return _merge_shipment_results(list(results))

    image = await asyncio.to_thread(
        optimize_for_vision, file_bytes, settings.gemini_extraction_max_dimension
    )
    return await _extract_shipment_from_image(image, system_prompt)


@observe()
async def extract_shipment_text(text: str) -> dict:
    """Extract structured shipment data from pasted order text (no vision).

    The prompt is fetched from Langfuse with NO code fallback: if it cannot be
    fetched, ``PromptUnavailableError`` propagates so the caller fails loudly.
    """
    system_prompt = get_prompt_required("extract-shipment-text")
    prompt = f"{EXTRACT_SHIPMENT_USER_PROMPT}\n\nDocument text:\n{text}"
    raw_text = await _call_text(
        prompt,
        model=settings.gemini_extraction_model,
        system_instruction=system_prompt,
    )
    return _parse_shipment_json(raw_text)


@observe()
async def match_shipment_article_name(
    *,
    supplier_name: str,
    article_description: str,
    candidates: list[tuple[str, str]],
) -> tuple[str | None, float]:
    """LLM-only resolver for shipment lines without supplier codes.

    Returns (sku_code, confidence). sku_code is None when unresolved.
    """
    if not article_description.strip() or not candidates:
        return None, 0.0

    candidate_lines = "\n".join(f"- {code}: {name}" for code, name in candidates[:200])
    prompt_template = get_prompt_required("match-shipment-article-name")
    prompt = (
        f"{prompt_template}\n\n"
        f"Supplier: {supplier_name or '(unknown)'}\n"
        f"Article description: {article_description}\n\n"
        f"Candidates:\n{candidate_lines}"
    )

    client = _get_client()
    async with _get_semaphore():
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.aio.models.generate_content(
                    model=settings.gemini_vision_model,
                    contents=[prompt],
                )
                break
            except ClientError as e:
                if e.code == 429 and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * attempt
                    logger.warning(
                        "Gemini rate limited on article matcher (attempt %d/%d), retrying in %ds",
                        attempt, MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.exception(
                        "Article-name matcher API call failed (attempt=%d)", attempt
                    )
                    raise
    cleaned = _strip_markdown_fences((response.text or "").strip())

    import json as _json

    try:
        payload = _json.loads(cleaned)
        if not isinstance(payload, dict):
            return None, 0.0
        sku_code = str(payload.get("sku_code", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        if not sku_code:
            return None, 0.0
        confidence = max(0.0, min(1.0, confidence))
        return sku_code, confidence
    except Exception:
        logger.warning("Article-name matcher returned invalid JSON")
        return None, 0.0
