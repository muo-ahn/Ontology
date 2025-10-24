"""Graph endpoints backed by GraphRepo and GraphContextBuilder."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from hashlib import sha1
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, condecimal, confloat, constr

from services.context_pack import GraphContextBuilder
from services.graph_repo import GraphRepo

logger = logging.getLogger(__name__)

class FindingIn(BaseModel):
    id: Optional[str] = None
    type: constr(strip_whitespace=True, min_length=1)
    location: Optional[constr(strip_whitespace=True)]
    size_cm: Optional[confloat(gt=0, le=100)]
    conf: condecimal(ge=0, le=1) = 0.8


class ImageIn(BaseModel):
    id: constr(strip_whitespace=True, min_length=1)
    path: Optional[str]
    modality: Optional[str]


class ReportIn(BaseModel):
    id: Optional[str] = None
    text: constr(strip_whitespace=True, min_length=1)
    model: Optional[str] = None
    conf: condecimal(ge=0, le=1) = 0.8
    ts: Optional[datetime]


class UpsertReq(BaseModel):
    case_id: constr(strip_whitespace=True, min_length=1)
    image: ImageIn
    report: ReportIn
    findings: List[FindingIn] = []


router = APIRouter()

_GRAPH_REPO = GraphRepo.from_env()
_CONTEXT_BUILDER = GraphContextBuilder(_GRAPH_REPO)


def _generate_report_id(image_id: str, report: ReportIn) -> str:
    seed = f"{image_id}|{report.text[:64]}|{report.model or ''}"
    return "r_" + sha1(seed.encode("utf-8")).hexdigest()[:16]


def _generate_finding_id(image_id: str, finding: FindingIn) -> str:
    seed = (
        f"{image_id}|{finding.type}|{finding.location or ''}|"
        f"{round(finding.size_cm or 0, 1)}"
    )
    return "f_" + sha1(seed.encode("utf-8")).hexdigest()[:16]


@router.post("/upsert")
async def upsert_case(payload: UpsertReq) -> dict[str, object]:
    data = payload.model_dump(mode="python")
    image_id: str = data["image"]["id"]

    report_data = data["report"]
    if not report_data.get("id"):
        report_data["id"] = _generate_report_id(image_id, payload.report)

    finding_ids: List[str] = []
    for idx, finding_data in enumerate(data.get("findings", [])):
        if not finding_data.get("id"):
            generated_id = _generate_finding_id(image_id, payload.findings[idx])
            finding_data["id"] = generated_id
        finding_ids.append(finding_data["id"])

    try:
        _GRAPH_REPO.upsert_case(data)
    except Exception as exc:  # pragma: no cover - depends on external Neo4j state
        logger.exception("Graph upsert failed for case=%s image=%s", data["case_id"], image_id)
        raise HTTPException(status_code=500, detail=f"Graph upsert failed: {exc}") from exc

    return {
        "ok": True,
        "case_id": data["case_id"],
        "image_id": image_id,
        "report_id": report_data["id"],
        "finding_ids": finding_ids,
    }


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
