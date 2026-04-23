import asyncio
import logging
import re
import time

import base64

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from PIL import Image, ImageOps
import io

from langfuse import observe, get_client as get_langfuse_client

from app.config import settings
from app.services.langfuse_client import get_prompt
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds
MAX_VISION_DIMENSION = 1024  # px – downscale before sending to Gemini

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


# ---------------------------------------------------------------------------
# Classification-only prompt (used when caller only needs is_package check)
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "summary": "brief 5-word description"}

Set "is_package" to true if the image shows any kind of box, case, crate, carton, or product packaging.
Set it to false for loose objects, scenes, furniture, electronics without packaging, food without packaging, etc.

Examples of true: wine box, shoe box, cardboard carton, wooden crate, sealed package, shipping parcel.
Examples of false: a clock, candles on a table, a laptop, a pair of shoes, a glass of wine."""

# ---------------------------------------------------------------------------
# Combined prompt — classify AND describe in a single call
# ---------------------------------------------------------------------------

CLASSIFY_AND_DESCRIBE_DEFAULT = """Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "description": "detailed description here"}

CLASSIFICATION:
Set "is_package" to true if the image shows any kind of box, case, crate, carton, or product packaging.
Set it to false for loose objects, scenes, furniture, electronics without packaging, food without packaging, etc.
If is_package is false, set "description" to a brief 5-word summary of what you see.

DESCRIPTION (only when is_package is true):
Your description will be embedded and compared against a reference database using cosine similarity.
Accuracy and specificity are critical — a wrong match means the wrong product gets shipped.

Transcribe ALL visible text exactly as printed (brand names, product names, years, volumes, certifications, codes).
Describe visual elements: dominant colors, logos, crests, illustrations, label placement, box material.
If this appears to be wine, pay special attention to: producer/domaine, wine name/cuvée, vintage year, appellation/region, classification.
For logos or symbols without readable text: describe the geometric structure (shapes, symmetry, line weight), position on the box, relative size, and color contrast. Be precise about what the shapes depict.

ONLY describe what you can actually see. Do NOT mention things that are "not visible" or "not present" — simply omit them.

Format the description as a compact paragraph starting with the most distinctive identifiers, optimized for text-similarity search."""

# ---------------------------------------------------------------------------
# Description-only prompt (used when classification is skipped)
# ---------------------------------------------------------------------------

DESCRIBE_DEFAULT = """Describe this product packaging for identification matching.
Your description will be embedded and compared against a reference database using cosine similarity.
Accuracy and specificity are critical — a wrong match means the wrong product gets shipped.

Transcribe ALL visible text exactly as printed (brand names, product names, years, volumes, certifications, codes).
Describe visual elements: dominant colors, logos, crests, illustrations, label placement, box material.
If this appears to be wine, pay special attention to: producer/domaine, wine name/cuvée, vintage year, appellation/region, classification.
For logos or symbols without readable text: describe the geometric structure (shapes, symmetry, line weight), position on the box, relative size, and color contrast. Be precise about what the shapes depict.

ONLY describe what you can actually see. Do NOT mention things that are "not visible" or "not present" — simply omit them.

Format as a compact paragraph starting with the most distinctive identifiers, optimized for text-similarity search."""


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
        if isinstance(data, dict) and "is_package" in data:
            is_package = bool(data["is_package"])
            description = str(data.get("description", "")).strip()
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]
            return is_package, description
    except (_json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallback: look for keywords suggesting packaging
    logger.warning("Combined response not valid JSON, using heuristic: %s", text[:100])
    lower = text.lower()
    package_words = {"box", "case", "crate", "carton", "package", "packaging", "parcel"}
    has_package_word = any(w in lower for w in package_words)
    return has_package_word, text[:50]


def _get_client() -> genai.Client:
    """Shared Gemini API client (default v1beta endpoint)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def optimize_for_vision(image_bytes: bytes, max_dimension: int | None = None) -> Image.Image:
    """Downscale image so its longest side is at most ``max_dimension`` px.

    Defaults to ``MAX_VISION_DIMENSION`` (suitable for box classification).
    Document extraction passes a higher limit so small table digits stay legible.
    """
    limit = max_dimension or MAX_VISION_DIMENSION
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    w, h = image.size
    if max(w, h) > limit:
        scale = limit / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)
        logger.info("Resized image from %dx%d to %dx%d for vision (limit=%d)", w, h, new_w, new_h, limit)
    return image


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
    logger.info("[TIMING] gemini_vision=%.0fms", vision_ms)

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

    raw_text = await _call_vision(image, CLASSIFY_PROMPT)
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
    prompt = get_prompt("describe-package", fallback=DESCRIBE_DEFAULT)
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
        langfuse.update_current_generation(
            model=settings.gemini_embedding_model,
            input=text,
            metadata={"output_dimensionality": EMBEDDING_DIM},
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

    prompt = get_prompt("classify-and-describe", fallback=CLASSIFY_AND_DESCRIBE_DEFAULT)
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


EXTRACT_SHIPMENT_SYSTEM_DEFAULT = "\n".join([
    "You are a delivery-note and invoice analysis agent for an inbound warehouse receiving system.",
    "You will receive a single document image (pakbon, factuur, or similar).",
    "Extract all product lines visible on the document.",
    "Output MUST be valid JSON matching the structure below exactly.",
    "",
    "JSON structure:",
    "{",
    '  "supplier_name": "string",',
    '  "reference": "string",',
    '  "document_type": "pakbon|invoice|unknown",',
    '  "raw_text": "short transcription summary",',
    '  "lines": [',
    "    {",
    '      "supplier_code": "string",',
    '      "description": "string",',
    '      "evidence": {',
    '        "line_text": "raw line text",',
    '        "quantity_text": "raw quantity fragment",',
    '        "unit_hint": "column header or inline label that identifies the unit"',
    "      },",
    '      "quantity": 102,',
    '      "quantity_unit": "pieces",',
    '      "confidence": 0.91',
    "    }",
    "  ]",
    "}",
    "",
    "Quantity rules (IMPORTANT):",
    "- Return ONE numeric quantity per line plus its unit. The backend converts pieces→boxes using a fixed ratio of 6 bottles per box, so you MUST NOT do that math yourself.",
    '- quantity_unit MUST be one of: "boxes" (dozen/colli/kisten/ds/ct), "pieces" (flessen/fl/btls/stuks/pcs), or "unknown".',
    "- Decide the unit from document context in this priority:",
    "  1. Column header directly above the number (e.g. 'Aantal', 'Colli', 'Dozen', 'Flessen', 'Fl', 'Btls').",
    "  2. Inline label right next to the number (e.g. '18 fl', '3 ds', '2 colli').",
    "  3. If the same line shows BOTH a small number (typically 1–5) and a larger number (e.g. 12, 24, 102) without explicit labels, the small number is boxes and the larger one is pieces — return the pieces value with quantity_unit='pieces'.",
    "- If you truly cannot tell whether the number is boxes or pieces, set quantity_unit='unknown' and lower the confidence score. Do NOT guess.",
    "- quantity MUST be a non-negative integer.",
    "- Transcribe the raw fragment you used into evidence.quantity_text and the header/label you relied on into evidence.unit_hint.",
    "",
    "Evidence rules:",
    "- Keep evidence fields as short verbatim snippets from the document.",
    "",
    "Filtering rules:",
    "- Include only product lines.",
    "- Ignore totals, pallet costs, transport, and signature fields.",
    "- If uncertain about a line, include it with a lower confidence score.",
    "",
    "Examples:",
    '- "ART123 Merlot 6x75cl 18 fl" → quantity=18, quantity_unit="pieces", evidence.quantity_text="18 fl", evidence.unit_hint="fl".',
    '- "ART456 Chardonnay 3 ds" → quantity=3, quantity_unit="boxes", evidence.quantity_text="3 ds", evidence.unit_hint="ds".',
    '- "AFI810125 - Trent, VdD Pinot Grigio25 1 102 132,60 76,50" with column headers (Colli | Flessen | Brutto | Netto) → quantity=102, quantity_unit="pieces", evidence.quantity_text="102", evidence.unit_hint="Flessen".',
    '- Single bare number with no header or label → quantity=<n>, quantity_unit="unknown", confidence lowered.',
])

EXTRACT_SHIPMENT_USER_PROMPT = "\n".join([
    "Return ONLY JSON matching the schema.",
    "Do not omit fields; use empty string for missing string fields and [] for missing arrays.",
])


@observe()
async def extract_shipment_document(image_bytes: bytes) -> dict:
    """Extract structured shipment data from a pakbon/factuur photo."""
    image = await asyncio.to_thread(
        optimize_for_vision, image_bytes, settings.gemini_extraction_max_dimension
    )
    system_prompt = get_prompt("extract-shipment-document", fallback=EXTRACT_SHIPMENT_SYSTEM_DEFAULT)
    raw_text = await _call_vision(
        image,
        EXTRACT_SHIPMENT_USER_PROMPT,
        model=settings.gemini_extraction_model,
        system_instruction=system_prompt,
    )
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


MATCH_SHIPMENT_ARTICLE_DEFAULT = """You are matching one inbound shipment line to an internal SKU catalog.
Return ONLY valid JSON:
{
  "sku_code": "string",
  "confidence": 0.0
}

Rules:
- Use the line description and optional supplier name.
- Choose from provided candidates only.
- If uncertain, return {"sku_code": "", "confidence": 0.0}.
"""


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
    prompt_template = get_prompt(
        "match-shipment-article-name",
        fallback=MATCH_SHIPMENT_ARTICLE_DEFAULT,
    )
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
