import logging
import re
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from PIL import Image
import io

from app.config import settings
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds
MAX_VISION_DIMENSION = 1024  # px – downscale before sending to Gemini

_client: genai.Client | None = None

# ---------------------------------------------------------------------------
# Step 1: Classification — is this a box/package?
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "summary": "brief 5-word description"}

Set "is_package" to true if the image shows any kind of box, case, crate, carton, or product packaging.
Set it to false for loose objects, scenes, furniture, electronics without packaging, food without packaging, etc.

Examples of true: wine box, shoe box, cardboard carton, wooden crate, sealed package, shipping parcel.
Examples of false: a clock, candles on a table, a laptop, a pair of shoes, a glass of wine."""

# ---------------------------------------------------------------------------
# Step 2: Description — describe everything on the packaging
# ---------------------------------------------------------------------------

DESCRIBE_PROMPT = """Describe this product packaging for identification matching.
Your description will be embedded and compared against a reference database using cosine similarity.
Accuracy and specificity are critical — a wrong match means the wrong product gets shipped.

Transcribe ALL visible text exactly as printed (brand names, product names, years, volumes, certifications, codes).
Describe visual elements: dominant colors, logos, crests, illustrations, label placement, box material.
If this appears to be wine, pay special attention to: producer/domaine, wine name/cuvée, vintage year, appellation/region, classification.

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


def _get_client() -> genai.Client:
    """Shared Gemini API client (default v1beta endpoint)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def optimize_for_vision(image_bytes: bytes) -> Image.Image:
    """Downscale image so its longest side is at most MAX_VISION_DIMENSION px.

    Returns a PIL Image ready for the Vision API.  Images already within the
    limit are returned as-is (no re-encoding quality loss).
    """
    image = Image.open(io.BytesIO(image_bytes))
    w, h = image.size
    if max(w, h) > MAX_VISION_DIMENSION:
        scale = MAX_VISION_DIMENSION / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)
        logger.info("Resized image from %dx%d to %dx%d for vision", w, h, new_w, new_h)
    return image


def _call_vision(image: Image.Image, prompt: str) -> str:
    """Call Gemini Vision with retry logic. Returns raw response text."""
    client = _get_client()
    logger.info("Calling Gemini Vision model=%s", settings.gemini_vision_model)
    t0 = time.perf_counter()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=settings.gemini_vision_model,
                contents=[prompt, image],
            )
            break
        except ClientError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * attempt
                logger.warning("Gemini rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRIES, delay)
                time.sleep(delay)
            else:
                logger.exception("Gemini Vision API call failed (model=%s, attempt=%d)", settings.gemini_vision_model, attempt)
                raise
    vision_ms = (time.perf_counter() - t0) * 1000
    logger.info("[TIMING] gemini_vision=%.0fms", vision_ms)
    return response.text


def classify_image(image_bytes: bytes) -> tuple[bool, str]:
    """Step 1: Classify whether the image shows a box/package.

    Returns (is_package, summary).
    """
    t0 = time.perf_counter()
    image = optimize_for_vision(image_bytes)
    resize_ms = (time.perf_counter() - t0) * 1000
    logger.info("[TIMING] image_resize=%.0fms", resize_ms)

    raw_text = _call_vision(image, CLASSIFY_PROMPT)
    logger.info("Classification raw response: %s", raw_text[:120])

    is_package, summary = parse_classify_response(raw_text)
    logger.info("Classification result: is_package=%s, summary: %s", is_package, summary)
    return is_package, summary


def describe_package(image_bytes: bytes) -> str:
    """Step 2: Describe the packaging for embedding.

    Returns a description optimized for text-similarity search.
    Always call this AFTER classify_image confirms it's a package,
    or when the user has overridden classification.
    """
    image = optimize_for_vision(image_bytes)
    raw_text = _call_vision(image, DESCRIBE_PROMPT)
    logger.info("Description raw response: %s", raw_text[:120])

    description = _strip_markdown_fences(raw_text).strip()
    # If the response is wrapped in quotes, strip them
    if description.startswith('"') and description.endswith('"'):
        description = description[1:-1]

    logger.info("Package description: %s", description[:100])
    return description


def describe_image(image_bytes: bytes) -> tuple[str, bool]:
    """Classify and describe in one call. Kept for backward compatibility.

    Returns (description, is_package).
    """
    is_package, summary = classify_image(image_bytes)
    if not is_package:
        return summary, False

    description = describe_package(image_bytes)
    return description, True


def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using gemini-embedding-001."""
    client = _get_client()

    logger.info("Calling Gemini Embedding model=%s", settings.gemini_embedding_model)
    t0 = time.perf_counter()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
            )
            break
        except ClientError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * attempt
                logger.warning("Gemini rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRIES, delay)
                time.sleep(delay)
            else:
                logger.exception("Gemini Embedding API call failed (model=%s, attempt=%d)", settings.gemini_embedding_model, attempt)
                raise
    embedding_ms = (time.perf_counter() - t0) * 1000

    logger.info("[TIMING] gemini_embedding=%.0fms", embedding_ms)
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


def describe_and_embed(image_bytes: bytes) -> tuple[str, list[float], str]:
    """Skip classification, go straight to describe + embed.

    Used when the user has overridden classification (skip_wine_check=True).
    Returns (description, embedding, quality).
    """
    t_start = time.perf_counter()
    logger.info("Processing overridden image (%d bytes) — skipping classification", len(image_bytes))

    description = describe_package(image_bytes)
    quality = assess_description_quality(description)
    embedding = generate_embedding(description)

    total_ms = (time.perf_counter() - t_start) * 1000
    logger.info("[TIMING] describe_and_embed_total=%.0fms quality=%s", total_ms, quality)
    return description, embedding, quality


def process_image(image_bytes: bytes) -> tuple[str, list[float] | None, bool]:
    """Full pipeline: classify → describe → embed.

    Returns (description, embedding, is_package).
    If the image is not a package, embedding is None (skipped to save cost).
    """
    t_start = time.perf_counter()
    logger.info("Processing image (%d bytes)", len(image_bytes))

    is_package, summary = classify_image(image_bytes)

    if not is_package:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info("[TIMING] process_image_total=%.0fms (rejected: not a package — %s)", total_ms, summary)
        return summary, None, False

    description = describe_package(image_bytes)
    embedding = generate_embedding(description)
    total_ms = (time.perf_counter() - t_start) * 1000
    logger.info("[TIMING] process_image_total=%.0fms", total_ms)
    return description, embedding, True
