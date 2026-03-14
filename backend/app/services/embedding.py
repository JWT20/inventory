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

VISION_PROMPT = """Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_wine": true, "description": "..."}

Set "is_wine" to true ONLY if the image shows a wine product (wine bottle, wine box, wine case, wine crate, or wine packaging). Set it to false for everything else (electronics, shoes, food, appliances, random objects, etc).

If is_wine is true, write a description that includes:
- Brand / Producer name (transcribe exactly as printed)
- Wine name / Cuvée (transcribe exactly)
- Vintage year (if visible, otherwise omit)
- Appellation / Region (only if printed)
- Color & design details (dominant colors, crests, illustrations)
- Any other distinguishing text (volume, classification like "Grand Cru Classé")
Format as a compact paragraph optimized for text-similarity search, starting with the most distinctive identifiers.

If is_wine is false, write a brief description of what the object actually is (e.g. "laptop computer", "shoe box")."""


_WINE_KEYWORDS = {
    "wine", "wijn", "vin", "vino", "château", "chateau", "domaine",
    "bodega", "cantina", "weingut", "cuvée", "cuvee", "bordeaux",
    "burgundy", "rioja", "champagne", "prosecco", "merlot", "cabernet",
    "chardonnay", "pinot", "syrah", "shiraz", "sauvignon", "riesling",
    "tempranillo", "sangiovese", "nebbiolo", "malbec", "grenache",
    "750ml", "375ml", "1500ml", "magnum",
}


def parse_vision_response(raw: str) -> tuple[bool, str]:
    """Extract wine classification and description from vision response.

    Tries JSON first, then falls back to the old WINE_PRODUCT: line format,
    then to keyword heuristics.  Returns (is_wine, clean_description).
    """
    import json as _json

    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = "\n".join(text.split("\n")[:-1])
    text = text.strip()

    # --- Try JSON ---
    try:
        data = _json.loads(text)
        if isinstance(data, dict) and "is_wine" in data:
            is_wine = bool(data["is_wine"])
            description = str(data.get("description", "")).strip()
            return is_wine, description
    except (_json.JSONDecodeError, TypeError, ValueError):
        pass

    # --- Fallback: WINE_PRODUCT: YES/NO line ---
    lines = text.splitlines()
    first_line = lines[0].strip().upper() if lines else ""

    if "WINE_PRODUCT:" in first_line:
        is_wine = "YES" in first_line
        description = "\n".join(lines[1:]).strip()
        return is_wine, description

    # --- Last resort: keyword heuristic (reject if no wine words found) ---
    logger.warning("Vision response not in expected format, using keyword heuristic")
    lower = text.lower()
    has_wine_keyword = any(kw in lower for kw in _WINE_KEYWORDS)
    return has_wine_keyword, text


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
    If the image is not wine, embedding is None (skipped to save cost/time).
    """
    t_start = time.perf_counter()
    logger.info("Processing image (%d bytes)", len(image_bytes))
    description, is_wine = describe_image(image_bytes)

    if not is_wine:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info("[TIMING] process_image_total=%.0fms (rejected: not wine)", total_ms)
        return description, None, False

    embedding = generate_embedding(description)
    total_ms = (time.perf_counter() - t_start) * 1000
    logger.info("[TIMING] process_image_total=%.0fms", total_ms)
    return description, embedding, True
