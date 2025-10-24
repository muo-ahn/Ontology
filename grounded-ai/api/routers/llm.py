"""LLM summary endpoint supporting V / VL / VGL modes."""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from services.context_pack import GraphContextBuilder
from services.graph_repo import GraphRepo
from services.llm_runner import LLMRunner


class AnswerMode(str, Enum):
    V = "V"
    VL = "VL"
    VGL = "VGL"


class AnswerRequest(BaseModel):
    mode: AnswerMode = Field(..., description="Summary mode: V, VL, or VGL")
    image_id: str = Field(..., description="Target image identifier")
    caption: Optional[str] = Field(
        None,
        description="Caption text provided in V/VL modes",
    )
    style: str = Field(
        default="one_line",
        description="Output style (currently only 'one_line' supported)",
    )

    @field_validator("style")
    @classmethod
    def _validate_style(cls, value: str) -> str:
        if value != "one_line":
            raise ValueError("style must be 'one_line'")
        return value

    @field_validator("caption")
    @classmethod
    def _strip_caption(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class AnswerResponse(BaseModel):
    answer: str
    latency_ms: int = Field(..., ge=0)


router = APIRouter()

_GRAPH_REPO = GraphRepo.from_env()
_CONTEXT_BUILDER = GraphContextBuilder(_GRAPH_REPO)

CAPTION_PROMPT = (
    "[Caption]\n{caption}\n\n"
    "[규칙]\n"
    "- 위 캡션만 근거로 답하라.\n"
    "- 새로운 사실/추정 금지. 불확실하면 '추가 검사 권고'.\n"
    "- 출력은 한국어 한 줄(최대 30자).\n"
)

GRAPH_PROMPT = (
    "[Graph Context]\n{context}\n\n"
    "[규칙]\n"
    "- 위 컨텍스트만 근거로 답하라.\n"
    "- 새로운 사실/추정 금지. 불확실하면 '추가 검사 권고'.\n"
    "- 출력은 한국어 한 줄(최대 30자). 근거를 괄호로 간단 표기.\n\n"
    "[질문]\n"
    "이 영상의 핵심 임상 소견을 요약하라."
)


def get_llm(request: Request) -> LLMRunner:
    runner: LLMRunner | None = getattr(request.app.state, "llm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="LLM runner unavailable")
    return runner


def _truncate_one_line(text: str, limit: int = 30) -> str:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit]


@router.post("/answer", response_model=AnswerResponse)
async def answer_endpoint(
    payload: AnswerRequest,
    llm: LLMRunner = Depends(get_llm),
) -> AnswerResponse:
    if payload.mode in {AnswerMode.V, AnswerMode.VL} and not payload.caption:
        raise HTTPException(status_code=400, detail="caption is required for V and VL modes")

    start = time.perf_counter()

    if payload.mode == AnswerMode.V:
        answer = _truncate_one_line(payload.caption or "")
        latency_ms = int((time.perf_counter() - start) * 1000)
        return AnswerResponse(answer=answer, latency_ms=latency_ms)

    if payload.mode == AnswerMode.VL:
        prompt = CAPTION_PROMPT.format(caption=payload.caption)
        result = await llm.generate(prompt)
        answer = _truncate_one_line(result.get("output", ""))
        latency_ms = int(result.get("latency_ms", (time.perf_counter() - start) * 1000))
        return AnswerResponse(answer=answer, latency_ms=latency_ms)

    # VGL mode
    try:
        context_text = await asyncio.to_thread(
            _CONTEXT_BUILDER.build_prompt_context,
            payload.image_id,
            2,
            "triples",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - depends on external Neo4j state
        raise HTTPException(status_code=500, detail=f"Graph context retrieval failed: {exc}") from exc

    prompt = GRAPH_PROMPT.format(
        context=context_text,
    )
    result = await llm.generate(prompt)
    answer = _truncate_one_line(result.get("output", ""))
    latency_ms = int(result.get("latency_ms", (time.perf_counter() - start) * 1000))
    return AnswerResponse(answer=answer, latency_ms=latency_ms)


__all__ = ["router"]
