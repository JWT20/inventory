import base64
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client = None

VISION_PROMPT = """You are identifying a wine box or bottle for inventory matching. Produce a precise, structured description that uniquely distinguishes this product from similar ones.

Extract and report EXACTLY what you see — do not guess or infer missing information.

1. **Brand / Producer**: Transcribe the exact producer or château name as printed.
2. **Wine name / Cuvée**: Transcribe the exact wine name, cuvée, or product line.
3. **Vintage**: The year, if visible. Write "not visible" if absent.
4. **Appellation / Region**: e.g. Bordeaux, Burgundy, Rioja — only if printed on the box.
5. **Color & Design**: Dominant colors of the box/label, notable design elements (crests, coats of arms, illustrations, patterns).
6. **Distinguishing text**: Any other unique text, serial numbers, or volume info (e.g. "750ml", "Grand Cru Classé").

Format as a compact paragraph optimized for text-similarity search. Start with the most distinctive identifiers (brand + wine name + vintage) and work toward less unique details. Be specific and literal — transcribe text exactly as printed."""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def describe_image(image_bytes: bytes) -> str:
    """Use OpenAI Vision to generate a detailed description of a wine box."""
    client = get_client()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model=settings.openai_vision_model,
        messages=[
            {
                "role": "system",
                "content": "You are a wine product identification specialist. "
                "Your descriptions will be embedded and matched against a database "
                "of reference product descriptions using cosine similarity. "
                "Accuracy and specificity are critical — a wrong match means the "
                "wrong product gets shipped.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        max_tokens=800,
    )

    description = response.choices[0].message.content
    logger.info("Vision description: %s", description[:100])
    return description


def generate_embedding(text: str) -> list[float]:
    """Generate a text embedding using OpenAI text-embedding-3-small."""
    client = get_client()

    response = client.embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
    )

    return response.data[0].embedding


def process_image(image_bytes: bytes) -> tuple[str, list[float]]:
    """Full pipeline: image → vision description → text embedding.

    Returns (description, embedding).
    """
    description = describe_image(image_bytes)
    embedding = generate_embedding(description)
    return description, embedding
