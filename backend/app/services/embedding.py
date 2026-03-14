import logging
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

VISION_PROMPT = """You are identifying a wine box or bottle for inventory matching. Your FIRST task is to determine whether this image shows a wine product (wine box, wine bottle, wine case, or wine packaging). Your SECOND task is to describe it if it is wine.

**Line 1 — Classification (REQUIRED):**
If this is a wine product, write exactly: WINE_PRODUCT: YES
If this is NOT a wine product (e.g. shoes, electronics, food, random objects), write exactly: WINE_PRODUCT: NO

**Line 2+ — Description (only if WINE_PRODUCT: YES):**
Extract and report EXACTLY what you see — do not guess or infer missing information.

1. **Brand / Producer**: Transcribe the exact producer or château name as printed.
2. **Wine name / Cuvée**: Transcribe the exact wine name, cuvée, or product line.
3. **Vintage**: The year, if visible. Write "not visible" if absent.
4. **Appellation / Region**: e.g. Bordeaux, Burgundy, Rioja — only if printed on the box.
5. **Color & Design**: Dominant colors of the box/label, notable design elements (crests, coats of arms, illustrations, patterns).
6. **Distinguishing text**: Any other unique text, serial numbers, or volume info (e.g. "750ml", "Grand Cru Classé").

Format as a compact paragraph optimized for text-similarity search. Start with the most distinctive identifiers (brand + wine name + vintage) and work toward less unique details. Be specific and literal — transcribe text exactly as printed.

If WINE_PRODUCT: NO, write a brief one-line description of what the object actually is."""


def parse_vision_response(raw: str) -> tuple[bool, str]:
    """Extract the WINE_PRODUCT flag and return (is_wine, clean_description).

    The clean description has the flag line stripped so embeddings stay clean.
    """
    lines = raw.strip().splitlines()
    first_line = lines[0].strip().upper() if lines else ""

    if "WINE_PRODUCT:" in first_line:
        is_wine = "YES" in first_line
        description = "\n".join(lines[1:]).strip()
    else:
        # Model didn't follow format — assume wine to avoid false rejections
        logger.warning("Vision response missing WINE_PRODUCT flag, assuming YES")
        is_wine = True
        description = raw.strip()

    return is_wine, description


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


def describe_image(image_bytes: bytes) -> tuple[str, bool]:
    """Use Gemini Vision to describe a wine box and classify whether it is wine.

    Returns (description, is_wine).
    """
    client = _get_client()

    t0 = time.perf_counter()
    image = optimize_for_vision(image_bytes)
    resize_ms = (time.perf_counter() - t0) * 1000

    logger.info("[TIMING] image_resize=%.0fms", resize_ms)
    logger.info("Calling Gemini Vision model=%s", settings.gemini_vision_model)
    t0 = time.perf_counter()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=settings.gemini_vision_model,
                contents=[
                    "You are a wine product identification specialist. "
                    "Your descriptions will be embedded and matched against a database "
                    "of reference product descriptions using cosine similarity. "
                    "Accuracy and specificity are critical — a wrong match means the "
                    "wrong product gets shipped.\n\n" + VISION_PROMPT,
                    image,
                ],
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

    raw_text = response.text
    logger.info("[TIMING] gemini_vision=%.0fms", vision_ms)
    logger.info("Vision raw response: %s", raw_text[:120])

    is_wine, description = parse_vision_response(raw_text)
    logger.info("Wine classification: is_wine=%s, description: %s", is_wine, description[:100])
    return description, is_wine


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


def process_image(image_bytes: bytes) -> tuple[str, list[float] | None, bool]:
    """Full pipeline: image → vision description → text embedding.

    Returns (description, embedding, is_wine).
    Embedding is always generated so non-wine images can still match
    against overridden reference images.
    """
    t_start = time.perf_counter()
    logger.info("Processing image (%d bytes)", len(image_bytes))
    description, is_wine = describe_image(image_bytes)

    embedding = generate_embedding(description)
    total_ms = (time.perf_counter() - t_start) * 1000
    if not is_wine:
        logger.info("[TIMING] process_image_total=%.0fms (not wine, embedding generated for matching)", total_ms)
    else:
        logger.info("[TIMING] process_image_total=%.0fms", total_ms)
    return description, embedding, is_wine
