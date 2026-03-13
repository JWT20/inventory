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

_client: genai.Client | None = None

VISION_PROMPT = """You are identifying a wine box or bottle for inventory matching. Produce a precise, structured description that uniquely distinguishes this product from similar ones.

Extract and report EXACTLY what you see — do not guess or infer missing information.

1. **Brand / Producer**: Transcribe the exact producer or château name as printed.
2. **Wine name / Cuvée**: Transcribe the exact wine name, cuvée, or product line.
3. **Vintage**: The year, if visible. Write "not visible" if absent.
4. **Appellation / Region**: e.g. Bordeaux, Burgundy, Rioja — only if printed on the box.
5. **Color & Design**: Dominant colors of the box/label, notable design elements (crests, coats of arms, illustrations, patterns).
6. **Distinguishing text**: Any other unique text, serial numbers, or volume info (e.g. "750ml", "Grand Cru Classé").

Format as a compact paragraph optimized for text-similarity search. Start with the most distinctive identifiers (brand + wine name + vintage) and work toward less unique details. Be specific and literal — transcribe text exactly as printed."""


def _get_client() -> genai.Client:
    """Shared Gemini API client (default v1beta endpoint)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def describe_image(image_bytes: bytes) -> str:
    """Use Gemini Vision to generate a detailed description of a wine box."""
    client = _get_client()

    image = Image.open(io.BytesIO(image_bytes))

    logger.info("Calling Gemini Vision model=%s", settings.gemini_vision_model)
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

    description = response.text
    logger.info("Vision description: %s", description[:100])
    return description


def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using gemini-embedding-001."""
    client = _get_client()

    logger.info("Calling Gemini Embedding model=%s", settings.gemini_embedding_model)
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

    return result.embeddings[0].values


def process_image(image_bytes: bytes) -> tuple[str, list[float]]:
    """Full pipeline: image → vision description → text embedding.

    Returns (description, embedding).
    """
    logger.info("Processing image (%d bytes)", len(image_bytes))
    description = describe_image(image_bytes)
    embedding = generate_embedding(description)
    logger.info("Image processed successfully")
    return description, embedding
