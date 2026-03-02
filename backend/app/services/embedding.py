import base64
import logging

import google.generativeai as genai
from PIL import Image
import io

from app.config import settings

logger = logging.getLogger(__name__)

_configured = False

VISION_PROMPT = """You are identifying a wine box or bottle for inventory matching. Produce a precise, structured description that uniquely distinguishes this product from similar ones.

Extract and report EXACTLY what you see — do not guess or infer missing information.

1. **Brand / Producer**: Transcribe the exact producer or château name as printed.
2. **Wine name / Cuvée**: Transcribe the exact wine name, cuvée, or product line.
3. **Vintage**: The year, if visible. Write "not visible" if absent.
4. **Appellation / Region**: e.g. Bordeaux, Burgundy, Rioja — only if printed on the box.
5. **Color & Design**: Dominant colors of the box/label, notable design elements (crests, coats of arms, illustrations, patterns).
6. **Distinguishing text**: Any other unique text, serial numbers, or volume info (e.g. "750ml", "Grand Cru Classé").

Format as a compact paragraph optimized for text-similarity search. Start with the most distinctive identifiers (brand + wine name + vintage) and work toward less unique details. Be specific and literal — transcribe text exactly as printed."""


def _ensure_configured():
    global _configured
    if not _configured:
        genai.configure(api_key=settings.gemini_api_key)
        _configured = True


def describe_image(image_bytes: bytes) -> str:
    """Use Gemini Vision to generate a detailed description of a wine box."""
    _ensure_configured()

    model = genai.GenerativeModel(settings.gemini_vision_model)
    image = Image.open(io.BytesIO(image_bytes))

    response = model.generate_content(
        [
            "You are a wine product identification specialist. "
            "Your descriptions will be embedded and matched against a database "
            "of reference product descriptions using cosine similarity. "
            "Accuracy and specificity are critical — a wrong match means the "
            "wrong product gets shipped.\n\n" + VISION_PROMPT,
            image,
        ],
    )

    description = response.text
    logger.info("Vision description: %s", description[:100])
    return description


def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using Google text-embedding-004."""
    _ensure_configured()

    result = genai.embed_content(
        model=f"models/{settings.gemini_embedding_model}",
        content=text,
    )

    return result["embedding"]


def process_image(image_bytes: bytes) -> tuple[str, list[float]]:
    """Full pipeline: image → vision description → text embedding.

    Returns (description, embedding).
    """
    description = describe_image(image_bytes)
    embedding = generate_embedding(description)
    return description, embedding
