import io
import logging

import open_clip
import torch
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_model = None
_preprocess = None
_tokenizer = None


def get_model():
    """Lazy-load the CLIP model to avoid loading at import time."""
    global _model, _preprocess, _tokenizer
    if _model is None:
        logger.info(
            "Loading CLIP model: %s (%s)", settings.clip_model, settings.clip_pretrained
        )
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            settings.clip_model, pretrained=settings.clip_pretrained
        )
        _tokenizer = open_clip.get_tokenizer(settings.clip_model)
        _model.eval()
        logger.info("CLIP model loaded successfully")
    return _model, _preprocess, _tokenizer


def generate_embedding(image_bytes: bytes) -> list[float]:
    """Generate a CLIP embedding from image bytes."""
    model, preprocess, _ = get_model()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_tensor = preprocess(image).unsqueeze(0)

    with torch.no_grad():
        embedding = model.encode_image(image_tensor)
        # Normalize the embedding
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)

    return embedding.squeeze().tolist()
