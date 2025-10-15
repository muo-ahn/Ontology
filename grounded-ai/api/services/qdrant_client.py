"""
Thin wrapper around qdrant-client to simplify upserts/search for the prototype.
Falls back to in-memory store if qdrant-client is unavailable.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QdrantVectorStore:
    host: str
    api_key: Optional[str]
    default_collection: str
    vector_size: int
    distance: str = "Cosine"
    _client: Optional[object] = field(init=False, default=None)
    _memory_store: Dict[str, Dict[str, Any]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        try:
            from qdrant_client import QdrantClient  # type: ignore

            kwargs: Dict[str, Any] = {"url": self.host}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = QdrantClient(**kwargs)
        except Exception:  # pragma: no cover - fallback path
            self._client = None

    @classmethod
    def from_env(cls) -> "QdrantVectorStore":
        host = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY")
        collection = os.getenv("QDRANT_COLLECTION", "medical_knowledge")
        vector_size = int(os.getenv("QDRANT_VECTOR_DIM", "768"))
        distance = os.getenv("QDRANT_DISTANCE", "Cosine")
        return cls(
            host=host,
            api_key=api_key,
            default_collection=collection,
            vector_size=vector_size,
            distance=distance,
        )

    async def ensure_collection(self, name: Optional[str] = None) -> None:
        if self._client is None:
            self._memory_store.setdefault(name or self.default_collection, {})
            return

        from qdrant_client.http import models as rest  # type: ignore

        collection_name = name or self.default_collection

        async def _ensure() -> None:
            client = self._client
            assert client is not None
            collections = client.get_collections().collections  # type: ignore[attr-defined]
            if any(col.name == collection_name for col in collections):
                return
            client.create_collection(
                collection_name=collection_name,
                vectors_config=rest.VectorParams(
                    size=self.vector_size,
                    distance=rest.Distance(self.distance),
                ),
            )

        await asyncio.to_thread(_ensure)

    async def upsert_text(
        self,
        collection: str,
        text: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        point_id = str(uuid.uuid4())
        payload = {"text": text, **(metadata or {})}
        await self._upsert_point(collection, point_id, vector, payload)
        return point_id

    async def upsert_image(
        self,
        collection: str,
        filename: str,
        vector: List[float],
        mime_type: Optional[str] = None,
    ) -> str:
        point_id = str(uuid.uuid4())
        payload = {"filename": filename, "mime_type": mime_type}
        await self._upsert_point(collection, point_id, vector, payload)
        return point_id

    async def search(
        self,
        collection: str,
        vector: List[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        if self._client is None:
            store = self._memory_store.get(collection, {})
            results = []
            for pid, doc in store.items():
                doc_vector = doc["vector"]
                score = sum(a * b for a, b in zip(doc_vector, vector))
                results.append({"id": pid, "score": float(score), "payload": doc["payload"]})
            return sorted(results, key=lambda x: x["score"], reverse=True)[:limit]

        async def _search() -> List[Dict[str, Any]]:
            client = self._client
            assert client is not None
            hits = client.search(
                collection_name=collection,
                query_vector=vector,
                limit=limit,
            )
            return [
                {"id": hit.id, "score": hit.score, "payload": hit.payload}
                for hit in hits
            ]

        return await asyncio.to_thread(_search)

    async def _upsert_point(
        self,
        collection: str,
        point_id: str,
        vector: List[float],
        payload: Dict[str, Any],
    ) -> None:
        await self.ensure_collection(collection)
        if self._client is None:
            self._memory_store[collection][point_id] = {"vector": vector, "payload": payload}
            return

        from qdrant_client.http import models as rest  # type: ignore

        async def _upsert() -> None:
            client = self._client
            assert client is not None
            point = rest.PointStruct(id=point_id, vector=vector, payload=payload)
            client.upsert(collection_name=collection, points=[point])

        await asyncio.to_thread(_upsert)

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()  # type: ignore[attr-defined]
        except Exception:
            pass
