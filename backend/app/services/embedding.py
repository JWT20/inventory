import base64
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client = None

VISION_PROMPT = """Beschrijf deze wijndoos zo gedetailleerd mogelijk voor identificatie.
Focus op:
- Merknaam / producent (exacte tekst op het etiket)
- Wijnnaam / cuvée
- Jaargang indien zichtbaar
- Kleur van de doos en het etiket
- Logo's, symbolen of afbeeldingen
- Vorm en grootte van de doos
- Eventuele onderscheidende kenmerken

Geef een gestructureerde beschrijving in het Nederlands. Wees zo specifiek mogelijk."""


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
            }
        ],
        max_tokens=500,
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
