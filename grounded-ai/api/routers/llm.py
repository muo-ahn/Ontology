"""Pipeline router coordinating vLM → LLM workflows."""

from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from models.pipeline import KGUpsertRequest, ImageModel, ReportModel, FindingModel
from services.dummy_dataset import default_summary
from services.llm_runner import LLMRunner
from services.vlm_runner import VLMRunner
from services.neo4j_client import Neo4jClient
from .kg import GraphContextResponse, fetch_image_context, get_neo4j, upsert_case_payload
from .vision import CaptionRequest, create_caption_response, get_llm, get_vlm


router = APIRouter()


class PipelineMode(str, Enum):
    V = "V"
    VL = "VL"
    VGL = "VGL"


CAPTION_TO_SUMMARY_PROMPT = (
    "[Image Caption]\n{caption}\n\n[Task]\n"
    "위 캡션만 근거로, 한국어 한 줄 소견을 작성하라.\n"
    "추정/상상 금지. 최대 30자."
)

GRAPH_TO_SUMMARY_PROMPT = (
    "[Graph Context]\n{context}\n\n[Task]\n"
    "위 컨텍스트만 근거로 한국어 한 줄 소견을 작성하라.\n"
    "새로운 사실 추가 금지. 불확실하면 \"추가 검사 권고\".\n"
    "최대 30자."
)


class PipelineRequest(CaptionRequest):
    mode: PipelineMode = Field(..., description="Pipeline variant to execute")


class PipelineResponse(BaseModel):
    mode: PipelineMode
    image_id: str
    case_id: Optional[str] = None
    caption: str
    findings: list[FindingModel]
    output: str
    vlm_model: str
    llm_model: Optional[str] = None
    graph_context: Optional[GraphContextResponse] = None
    timings: dict[str, int]


async def _prepare_caption(
    payload: PipelineRequest,
    runner: VLMRunner,
) -> tuple[CaptionResponse, Optional[dict[str, Any]], Optional[str], dict[str, Any]]:
    caption_response, entry, resolved_path, raw_vlm = await create_caption_response(payload, runner)
    case_id = (entry or {}).get("case_id") or payload.case_id
    if entry is not None and case_id:
        entry["case_id"] = case_id
    elif case_id:
        entry = {"case_id": case_id}
    return caption_response, entry, resolved_path, raw_vlm


def _select_summary(entry: Optional[dict[str, Any]], candidate: str) -> str:
    if candidate and not candidate.startswith("[mock-llm]"):
        return candidate.strip()
    curated = default_summary(entry)
    return curated or candidate


@router.post("/answer", response_model=PipelineResponse)
async def run_pipeline(
    payload: PipelineRequest,
    vlm: VLMRunner = Depends(get_vlm),
    llm: LLMRunner = Depends(get_llm),
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> PipelineResponse:
    start = time.perf_counter()
    caption_response, entry, resolved_path, raw_vlm = await _prepare_caption(payload, vlm)

    image_path = (
        caption_response.image.path
        or resolved_path
        or payload.file_path
        or f"/tmp/{caption_response.image.image_id}.png"
    )
    case_id = (entry or {}).get("case_id")
    if not case_id:
        case_id = f"C_{caption_response.image.image_id}" if not payload.case_id else payload.case_id
    timings: dict[str, int] = {"vlm_ms": int(caption_response.vlm_latency_ms)}
    timings["vlm_model_ms"] = int((raw_vlm or {}).get("latency_ms", timings["vlm_ms"]))

    mode = payload.mode
    llm_output: Optional[str] = None
    graph_context: Optional[GraphContextResponse] = None
    llm_model_name: Optional[str] = None

    if mode == PipelineMode.V:
        output_text = caption_response.report.text
    elif mode == PipelineMode.VL:
        llm_prompt = CAPTION_TO_SUMMARY_PROMPT.format(caption=caption_response.report.text)
        llm_start = time.perf_counter()
        llm_result = await llm.generate(llm_prompt)
        timings["llm_ms"] = int((time.perf_counter() - llm_start) * 1000)
        llm_output = _select_summary(entry, llm_result.get("output", ""))
        timings["llm_model_ms"] = int(llm_result.get("latency_ms", timings["llm_ms"]))
        llm_model_name = llm_result.get("model", llm.model)
        output_text = llm_output
    elif mode == PipelineMode.VGL:
        image_model = ImageModel(
            image_id=caption_response.image.image_id,
            path=image_path,
            modality=caption_response.image.modality,
        )
        report = ReportModel(
            id=caption_response.report.id,
            text=caption_response.report.text,
            model=caption_response.report.model,
            conf=caption_response.report.conf,
            ts=caption_response.report.ts,
        )
        kg_payload = KGUpsertRequest(
            case_id=case_id,
            image=image_model,
            report=report,
            findings=caption_response.findings,
        )
        graph_start = time.perf_counter()
        await upsert_case_payload(kg_payload, neo4j)
        graph_context = await fetch_image_context(caption_response.image.image_id, neo4j)
        timings["graph_ms"] = int((time.perf_counter() - graph_start) * 1000)
        context_json = json.dumps(graph_context.model_dump(mode="json"), ensure_ascii=False, indent=2)
        llm_prompt = GRAPH_TO_SUMMARY_PROMPT.format(context=context_json)
        llm_start = time.perf_counter()
        llm_result = await llm.generate(llm_prompt)
        timings["llm_ms"] = int((time.perf_counter() - llm_start) * 1000)
        llm_output = _select_summary(entry, llm_result.get("output", ""))
        timings["llm_model_ms"] = int(llm_result.get("latency_ms", timings["llm_ms"]))
        llm_model_name = llm_result.get("model", llm.model)
        output_text = llm_output
    else:  # pragma: no cover - Enum guards
        raise HTTPException(status_code=400, detail="Unsupported pipeline mode")

    total_ms = int((time.perf_counter() - start) * 1000)
    timings.setdefault("llm_ms", 0)
    timings.setdefault("graph_ms", 0)
    timings.setdefault("llm_model_ms", 0)
    timings["total_ms"] = total_ms

    return PipelineResponse(
        mode=mode,
        image_id=caption_response.image.image_id,
        case_id=case_id,
        caption=caption_response.report.text,
        findings=caption_response.findings,
        output=output_text,
        vlm_model=caption_response.report.model,
        llm_model=llm_model_name,
        graph_context=graph_context,
        timings=timings,
    )
