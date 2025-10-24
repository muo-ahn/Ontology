import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from routers import embed, graph, llm, vision
from services.clip_embedder import ClipEmbedder
from events.bus import EventBus
from events.tracker import TaskStatusTracker
from services.graph_repository import GraphRepository
from services.llm_runner import LLMRunner
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
    llm_runner = LLMRunner.from_env()
    graph_repo = GraphRepository.from_env()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    event_bus = EventBus(redis_url)
    status_tracker = TaskStatusTracker(redis_url)

    app.state.neo4j = neo4j_client
    app.state.qdrant = qdrant_client
    app.state.vlm = vlm_runner
    app.state.embedder = clip_embedder
    app.state.llm = llm_runner
    app.state.graph_repo = graph_repo
    app.state.event_bus = event_bus
    app.state.status_tracker = status_tracker

    try:
        yield
    finally:
        neo4j_client.close()
        qdrant_client.close()
        vlm_runner.close()
        clip_embedder.close()
        llm_runner.close()
        await event_bus.close()
        await status_tracker.close()
        # GraphRepository uses lazy HTTP sessions; no explicit close method.


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
app.include_router(graph.router, prefix="/graph", tags=["knowledge-graph"])
app.include_router(llm.router, prefix="/llm", tags=["pipeline"])
app.include_router(vision.router, prefix="/vision", tags=["vision"])
