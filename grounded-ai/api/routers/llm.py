"""LLM summary endpoint enforcing strict prompting rules."""

from __future__ import annotations

import base64
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from services.dummy_dataset import lookup_entry
from services.llm_runner import LLMRunner


class AnswerMode(str, Enum):
    V = "V"
    VL = "VL"
    VGL = "VGL"


class LLMAnswerReq(BaseModel):
    mode: AnswerMode
    image_id: Optional[str] = None
    caption: Optional[str] = None
    style: Optional[str] = "one_line"
    max_chars: int = Field(default=30, ge=1, le=120)
    fallback_to_vl: bool = True

    @field_validator("style")
    @classmethod
    def _validate_style(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value != "one_line":
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

_DEFAULT_DATA_ROOT = Path(
    os.getenv("MEDICAL_DUMMY_DIR", Path(__file__).resolve().parents[2] / "data" / "medical_dummy")
)

V_TEMPLATE = "{caption}\n\n규칙: 한 줄, 최대 {max_chars}자, 추정 금지."
VGL_TEMPLATE = """[GRAPH CONTEXT]
{graph_triples}

[규칙]
- 위 컨텍스트만 근거로 답하라.
- 새로운 사실/추정 금지.
- 한 줄만 출력, 최대 {max_chars}자.
- 가능하면 마지막에 괄호로 핵심 근거 한 개만 표기. 예: (RUL 결절)

[질문]
이 영상을 한국어 한 줄로 요약하라.
"""


def get_llm(request: Request) -> LLMRunner:
    runner: LLMRunner | None = getattr(request.app.state, "llm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="LLM runner unavailable")
    return runner


class LLMInputError(ValueError):
    """Raised when required inputs for LLM prompting are missing."""


def clamp_one_line(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit]


def _caption_from_normalised(
    normalized: Optional[Dict[str, Any]],
    *,
    error_message: str,
) -> str:
    if not normalized:
        raise LLMInputError(error_message)
    report = normalized.get("report") if isinstance(normalized, dict) else None
    if not isinstance(report, dict):
        raise LLMInputError(error_message)
    caption = str(report.get("text") or "").strip()
    if not caption:
        raise LLMInputError(error_message)
    return caption


def run_v_mode(normalized: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    caption = _caption_from_normalised(normalized, error_message="caption is required for V mode")
    return {"text": clamp_one_line(caption, max_chars), "latency_ms": 0}


async def run_vl_mode(
    llm: LLMRunner,
    normalized: Dict[str, Any],
    max_chars: int,
) -> Dict[str, Any]:
    caption = _caption_from_normalised(normalized, error_message="caption is required for VL mode")
    prompt = V_TEMPLATE.format(caption=caption, max_chars=max_chars)
    start = time.perf_counter()
    result = await llm.generate(prompt, temperature=0.2)
    answer = clamp_one_line(str(result.get("output", "")), max_chars)
    latency_ms = _llm_latency(result, start)
    return {"text": answer, "latency_ms": latency_ms}


async def run_vgl_mode(
    llm: LLMRunner,
    image_id: Optional[str],
    context_str: str,
    max_chars: int,
    fallback_to_vl: bool,
    normalized: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not image_id:
        raise LLMInputError("image_id is required for VGL mode")

    context_clean = (context_str or "").strip()
    start = time.perf_counter()
    if context_clean:
        prompt = VGL_TEMPLATE.format(graph_triples=context_clean, max_chars=max_chars)
        result = await llm.generate(prompt, temperature=0.2)
        answer = clamp_one_line(str(result.get("output", "")), max_chars)
        latency_ms = _llm_latency(result, start)
        return {"text": answer, "latency_ms": latency_ms, "degraded": False}

    if not fallback_to_vl:
        raise LLMInputError("empty graph context")

    caption = None
    try:
        caption = _caption_from_normalised(
            normalized,
            error_message="caption is required for VL mode",
        )
    except LLMInputError:
        caption = None
    if not caption:
        raise LLMInputError("caption is required for VL mode")

    fallback_normalized: Dict[str, Any] = {"report": {"text": caption}}
    fallback_result = await run_vl_mode(llm, fallback_normalized, max_chars)
    return {**fallback_result, "degraded": "VL"}


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict) and data.get("detail"):
        detail = data["detail"]
        if isinstance(detail, (str, int, float)):
            return str(detail)
        return str(detail)
    return response.text


def _resolve_image_payload(image_id: str) -> Optional[dict[str, str]]:
    """Return a payload suitable for /vision/caption calls."""

    entry = lookup_entry(id=image_id)
    filenames: list[str] = []
    if entry and entry.get("file_name"):
        filenames.append(entry["file_name"])

    cleaned = image_id.strip().lower().replace("-", "_")
    if not cleaned.endswith(".png"):
        cleaned_filename = f"{cleaned}.png"
    else:
        cleaned_filename = cleaned
    filenames.append(cleaned_filename)

    image_dirs = [
        _DEFAULT_DATA_ROOT / "images",
        _DEFAULT_DATA_ROOT,
        Path("/data/medical_dummy/images"),
        Path("/data/images"),
        Path("/data"),
    ]

    for directory in image_dirs:
        for name in filenames:
            candidate = directory / name
            if candidate.exists():
                try:
                    image_bytes = candidate.read_bytes()
                except OSError:
                    continue
                image_b64 = base64.b64encode(image_bytes).decode("utf-8")
                return {"id": image_id, "image_b64": image_b64}
    return None


async def _fetch_graph_context(request: Request, image_id: str) -> str:
    url = request.url_for("get_context")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, params={"id": image_id, "mode": "triples"})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _extract_error_detail(exc.response)
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"graph context request failed: {exc}") from exc
    data = response.json()
    context = (data.get("context") if isinstance(data, dict) else None) or ""
    return str(context).strip()


async def _fetch_caption_from_vision(request: Request, image_id: str) -> str:
    payload = _resolve_image_payload(image_id)
    if payload is None:
        raise HTTPException(status_code=422, detail="empty graph context")

    url = request.url_for("generate_caption")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _extract_error_detail(exc.response)
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"vision caption request failed: {exc}") from exc
    data = response.json()
    caption = ""
    if isinstance(data, dict):
        report = data.get("report")
        if isinstance(report, dict):
            caption = str(report.get("text") or "").strip()
    if not caption:
        raise HTTPException(status_code=502, detail="caption fallback unavailable")
    return caption


def _llm_latency(result: dict[str, object], start: float) -> int:
    latency = result.get("latency_ms")
    if isinstance(latency, (int, float)):
        return int(latency)
    return int((time.perf_counter() - start) * 1000)


@router.post("/answer", response_model=AnswerResponse)
async def answer_endpoint(
    payload: LLMAnswerReq,
    request: Request,
    llm: LLMRunner = Depends(get_llm),
) -> AnswerResponse:
    start = time.perf_counter()

    max_chars = payload.max_chars
    normalized: Optional[Dict[str, Any]] = None
    if payload.caption:
        normalized = {"report": {"text": payload.caption}}

    if payload.mode == AnswerMode.V:
        try:
            result = run_v_mode(normalized or {}, max_chars)
        except LLMInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)
        if not result.get("latency_ms"):
            result["latency_ms"] = latency_ms
        return AnswerResponse(answer=result["text"], latency_ms=int(result["latency_ms"]))

    if payload.mode == AnswerMode.VL:
        try:
            result = await run_vl_mode(llm, normalized or {}, max_chars)
        except LLMInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return AnswerResponse(answer=result["text"], latency_ms=int(result["latency_ms"]))

    # VGL mode
    if not payload.image_id:
        raise HTTPException(status_code=422, detail="image_id is required for VGL mode")

    context_text = await _fetch_graph_context(request, payload.image_id)

    normalized_for_vgl = normalized
    if not context_text and payload.fallback_to_vl and normalized_for_vgl is None:
        caption = await _fetch_caption_from_vision(request, payload.image_id)
        normalized_for_vgl = {"report": {"text": caption}}

    try:
        result = await run_vgl_mode(
            llm,
            payload.image_id,
            context_text,
            max_chars,
            payload.fallback_to_vl,
            normalized_for_vgl,
        )
    except LLMInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AnswerResponse(answer=result["text"], latency_ms=int(result["latency_ms"]))


__all__ = [
    "router",
    "get_llm",
    "clamp_one_line",
    "run_v_mode",
    "run_vl_mode",
    "run_vgl_mode",
    "LLMInputError",
]