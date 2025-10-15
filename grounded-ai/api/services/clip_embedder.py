"""
Utility wrapper around CLIP / multimodal embedding models.
Falls back to deterministic pseudo-embeddings if dependencies are absent.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from typing import Optional


def _hash_to_vector(payload: bytes, dim: int) -> list[float]:
    """
    Deterministic fallback that chops a SHA256 digest into floats.
    Keeps the orchestration code functional even without heavy ML deps.
    """
    digest = hashlib.sha256(payload).digest()
    repeats = (dim * 4 + len(digest) - 1) // len(digest)
    buf = (digest * repeats)[: dim * 4]
    vector = []
    for i in range(0, len(buf), 4):
        chunk = buf[i : i + 4]
        value = int.from_bytes(chunk, "little", signed=False) / 2**32
        vector.append(float(value))
    return vector


@dataclass
class ClipEmbedder:
    model_name: str
    device: str
    vector_dim: int = 768
    _model: Optional[object] = None

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self.model_name, device=self.device)
            self.vector_dim = self._model.get_sentence_embedding_dimension()
        except Exception:  # pragma: no cover - safe fallback
            self._model = None

    @classmethod
    def from_env(cls) -> "ClipEmbedder":
        model_name = os.getenv("CLIP_MODEL_NAME", "sentence-transformers/clip-ViT-B-32")
        device = os.getenv("CLIP_DEVICE", "cpu")
        vector_dim = int(os.getenv("CLIP_VECTOR_DIM", "768"))
        return cls(model_name=model_name, device=device, vector_dim=vector_dim)

    async def embed_text(self, text: str) -> list[float]:
        if self._model:
            return await asyncio.to_thread(self._model.encode, text, normalize_embeddings=True)
        return _hash_to_vector(text.encode("utf-8"), self.vector_dim)

    async def embed_image(self, image_bytes: bytes) -> list[float]:
        return _hash_to_vector(image_bytes, self.vector_dim)

    async def embed_pair(self, text: str, image_bytes: bytes) -> tuple[list[float], list[float]]:
        text_vector, image_vector = await asyncio.gather(
            self.embed_text(text),
            self.embed_image(image_bytes),
        )
        return text_vector, image_vector

    def close(self) -> None:
        # SentenceTransformer does not expose dedicated close hooks.
        pass
