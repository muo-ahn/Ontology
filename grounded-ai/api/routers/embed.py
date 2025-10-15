from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from services.clip_embedder import ClipEmbedder
from services.qdrant_client import QdrantVectorStore


router = APIRouter()


def get_embedder(request: Request) -> ClipEmbedder:
    embedder: ClipEmbedder | None = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=500, detail="Embedding service unavailable")
    return embedder


def get_vector_store(request: Request) -> QdrantVectorStore:
    store: QdrantVectorStore | None = getattr(request.app.state, "qdrant", None)
    if store is None:
        raise HTTPException(status_code=500, detail="Vector store unavailable")
    return store


class TextEmbeddingRequest(BaseModel):
    text: str = Field(..., description="Raw input text to embed")
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str | None = Field(
        default=None,
        description="Optional Qdrant collection override. Defaults to environment setting.",
    )


class TextEmbeddingResponse(BaseModel):
    collection: str
    point_id: str
    vector_dim: int


@router.post("/text", response_model=TextEmbeddingResponse)
async def embed_text(
    payload: TextEmbeddingRequest,
    embedder: ClipEmbedder = Depends(get_embedder),
    vector_store: QdrantVectorStore = Depends(get_vector_store),
) -> TextEmbeddingResponse:
    vector = await embedder.embed_text(payload.text)
    collection = payload.collection or vector_store.default_collection
    point_id = await vector_store.upsert_text(
        collection=collection,
        text=payload.text,
        vector=vector,
        metadata=payload.metadata,
    )
    return TextEmbeddingResponse(
        collection=collection,
        point_id=point_id,
        vector_dim=len(vector),
    )


class ImageEmbeddingResponse(BaseModel):
    collection: str
    point_id: str
    vector_dim: int


@router.post("/image", response_model=ImageEmbeddingResponse)
async def embed_image(
    file: UploadFile,
    embedder: ClipEmbedder = Depends(get_embedder),
    vector_store: QdrantVectorStore = Depends(get_vector_store),
    collection: str | None = None,
) -> ImageEmbeddingResponse:
    contents = await file.read()
    vector = await embedder.embed_image(contents)
    target_collection = collection or vector_store.default_collection
    point_id = await vector_store.upsert_image(
        collection=target_collection,
        filename=file.filename,
        vector=vector,
        mime_type=file.content_type,
    )
    return ImageEmbeddingResponse(
        collection=target_collection,
        point_id=point_id,
        vector_dim=len(vector),
    )
