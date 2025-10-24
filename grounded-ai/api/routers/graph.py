"""Graph endpoints backed by GraphRepo and GraphContextBuilder."""

from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, HTTPException, Query

from models.pipeline import KGUpsertRequest
from services.context_pack import GraphContextBuilder
from services.graph_repo import GraphRepo

logger = logging.getLogger(__name__)

router = APIRouter()

_GRAPH_REPO = GraphRepo.from_env()
_CONTEXT_BUILDER = GraphContextBuilder(_GRAPH_REPO)


@router.post("/upsert")
async def upsert_case(payload: KGUpsertRequest) -> dict[str, bool]:
    try:
        _GRAPH_REPO.upsert_case(payload.model_dump(mode="json"))
    except Exception as exc:  # pragma: no cover - depends on external Neo4j state
        logger.exception("Graph upsert failed for case=%s image=%s", payload.case_id, payload.image.id)
        raise HTTPException(status_code=500, detail=f"Graph upsert failed: {exc}") from exc
    return {"ok": True}


@router.get("/context")
async def get_context(
    id: str = Query(..., description="Target image identifier"),
    k: int = Query(2, ge=1, le=10, description="Top-k evidence paths to include"),
    mode: str = Query("triples", pattern="^(triples|json)$", description="triples → formatted summary, json → raw facts JSON"),
) -> dict[str, str]:
    try:
        context = _CONTEXT_BUILDER.build_prompt_context(id=id, k=k, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - depends on external Neo4j state
        logger.error("Graph context build failed for image_id=%s mode=%s k=%s: %s", id, mode, k, exc)
        logger.debug("Graph context failure traceback:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Graph context build failed: {exc}") from exc
    return {"context": context}


__all__ = ["router"]
