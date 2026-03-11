import logging

from google import genai
from google.genai import types
from PIL import Image
import io

from app.config import settings
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

VISION_PROMPT = """You are identifying a wine box or bottle for inventory matching. Your description will be embedded and matched against a database using cosine similarity. Accuracy is critical.

RULES:
- Transcribe ALL visible text exactly as printed, stamped, or handwritten — even partial, blurry, or tiny text.
- Do not guess or infer missing information. Only report what you can see.
- Do not use markdown formatting.

EXTRACT IN ORDER OF IMPORTANCE:

1. BRAND / PRODUCER: Exact name as printed (château, domaine, maison, etc.)
2. WINE NAME / CUVÉE: Product line or cuvée name.
3. VINTAGE: Year if visible, otherwise "not visible".
4. APPELLATION / REGION: Only if printed (e.g. Bordeaux, Burgundy, Rioja).
5. CLASSIFICATION: e.g. Grand Cru Classé, Premier Cru, AOC, IGP.
6. ALL OTHER TEXT: Serial numbers, lot numbers, volume (750ml, 1.5L), barcodes (if digits are readable), handwritten notes, checkmarks, stamps, stickers — transcribe everything.
7. PHYSICAL APPEARANCE: Box or bottle color, material (wood, cardboard, metal), notable design elements (crests, illustrations, embossing, foil).
8. TEXT PLACEMENT: Where text appears (top, bottom, side, stamped on corner, handwritten on flap, sticker on side).

If the item has minimal branding (e.g. a plain white box), focus on every small distinguishing detail: any stamped text, checkbox marks, sticker residue, handwriting, printed codes, or subtle markings. These small details are the primary identifiers.

Format as a single compact paragraph. Start with the most distinctive identifiers and work toward less unique details. Be literal and specific."""


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

    response = client.models.generate_content(
        model=settings.gemini_vision_model,
        contents=[VISION_PROMPT, image],
    )

    description = response.text
    logger.info("Vision description: %s", description[:100])
    return description


def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using gemini-embedding-001."""
    client = _get_client()

    result = client.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
    )

    return result.embeddings[0].values


def process_image(image_bytes: bytes) -> tuple[str, list[float]]:
    """Full pipeline: image → vision description → text embedding.

    Returns (description, embedding).
    """
    description = describe_image(image_bytes)
    embedding = generate_embedding(description)
    return description, embedding
