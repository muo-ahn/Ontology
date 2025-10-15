from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from routers import embed, kg, vision
from services.clip_embedder import ClipEmbedder
from services.neo4j_client import Neo4jClient
from services.qdrant_client import QdrantVectorStore
from services.vlm_runner import VLMRunner


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Create shared service instances during startup and ensure they are
    closed gracefully on shutdown. FastAPI reuses these via dependency overrides.
    """
    neo4j_client = Neo4jClient.from_env()
    qdrant_client = QdrantVectorStore.from_env()
    vlm_runner = VLMRunner.from_env()
    clip_embedder = ClipEmbedder.from_env()

    app.state.neo4j = neo4j_client
    app.state.qdrant = qdrant_client
    app.state.vlm = vlm_runner
    app.state.embedder = clip_embedder

    try:
        yield
    finally:
        neo4j_client.close()
        qdrant_client.close()
        vlm_runner.close()
        clip_embedder.close()


app = FastAPI(
    title="Ontology + vLM + LLM Orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    """Minimal readiness probe for Docker compose."""
    return {"status": "ok"}


# Router registration -------------------------------------------------------
app.include_router(embed.router, prefix="/embed", tags=["embeddings"])
app.include_router(kg.router, prefix="/kg", tags=["knowledge-graph"])
app.include_router(vision.router, prefix="/vision", tags=["vision"])
