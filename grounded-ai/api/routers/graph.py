"""Graph endpoints backed by GraphRepo and GraphContextBuilder."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from hashlib import sha1
from typing import Any, List, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, condecimal, confloat, constr

from services.context_pack import GraphContextBuilder
from services.dedup import dedup_findings
from services.graph_repo import GraphRepo

logger = logging.getLogger(__name__)

class FindingIn(BaseModel):
    id: Optional[str] = None
    type: constr(strip_whitespace=True, min_length=1)
    location: Optional[constr(strip_whitespace=True)]
    size_cm: Optional[confloat(gt=0, le=100)]
    conf: condecimal(ge=0, le=1) = 0.8


class ImageIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    image_id: constr(strip_whitespace=True, min_length=1) = Field(alias="id")
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


def _generate_report_id(image_id: str, report: Mapping[str, Any]) -> str:
    text = (report.get("text") or "")[:256]
    model = report.get("model") or ""
    seed = f"{image_id}|{text}|{model}"
    return "R_" + sha1(seed.encode("utf-8")).hexdigest()[:12]


def _generate_finding_id(image_id: str, finding: Mapping[str, Any]) -> str:
    f_type = finding.get("type") or ""
    location = finding.get("location") or ""
    size_cm = finding.get("size_cm") or 0
    try:
        size_val = round(float(size_cm), 1)
    except Exception:
        size_val = 0.0
    seed = f"{image_id}|{f_type}|{location}|{size_val}"
    return "f_" + sha1(seed.encode("utf-8")).hexdigest()[:16]


@router.post("/upsert")
async def upsert_case(payload: UpsertReq) -> dict[str, object]:
    data = payload.model_dump(mode="python")
    data["findings"] = dedup_findings(data.get("findings") or [])
    image_id: str = data["image"]["image_id"]

    report_data = data["report"]
    if not report_data.get("id"):
        report_data["id"] = _generate_report_id(image_id, report_data)

    finding_ids: List[str] = []
    for finding_data in data.get("findings", []):
        if not finding_data.get("id"):
            generated_id = _generate_finding_id(image_id, finding_data)
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
    image_id: Optional[str] = Query(
        None,
        description="Target image identifier (preferred parameter)",
    ),
    id: Optional[str] = Query(
        None,
        description="Legacy query parameter for the image identifier",
    ),
    k: int = Query(2, ge=1, le=10, description="Top-k evidence paths to include"),
    mode: str = Query("triples", pattern="^(triples|json)$", description="triples → formatted summary, json → raw facts JSON"),
) -> dict[str, str]:
    resolved_id = image_id or id
    if not resolved_id:
        raise HTTPException(status_code=422, detail="image_id is required")
    try:
        context = _CONTEXT_BUILDER.build_prompt_context(image_id=resolved_id, k=k, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - depends on external Neo4j state
        logger.error(
            "Graph context build failed for image_id=%s mode=%s k=%s: %s",
            resolved_id,
            mode,
            k,
            exc,
        )
        logger.debug("Graph context failure traceback:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Graph context build failed: {exc}") from exc
    return {"context": context}


__all__ = ["router"]
