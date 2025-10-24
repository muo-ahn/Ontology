"""One-shot orchestration endpoint that chains vLM → graph → LLM."""

from __future__ import annotations

import base64
import binascii
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from services.context_pack import GraphContextBuilder
from services.graph_repo import GraphRepo
from services.llm_runner import LLMRunner
from services.vlm_runner import VLMRunner

from .llm import LLMInputError, get_llm, run_v_mode, run_vgl_mode, run_vl_mode

CAPTION_PROMPT = "Summarise the key clinical findings in this medical image."
GRAPH_TRIPLE_CHAR_CAP = 1800

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class AnalyzeReq(BaseModel):
    case_id: Optional[str] = Field(default=None, description="Existing case identifier")
    image_b64: Optional[str] = Field(default=None, description="Inline base64 image payload")
    file_path: Optional[str] = Field(default=None, description="Filesystem path to image")
    modes: List[str] = Field(default_factory=lambda: ["V", "VL", "VGL"])
    k: int = Field(default=2, ge=1, le=10)
    max_chars: int = Field(default=30, ge=1, le=120)
    fallback_to_vl: bool = True
    timeout_ms: int = Field(default=20000, ge=1000, le=60000)
    idempotency_key: Optional[str] = None

    @field_validator("modes", mode="after")
    @classmethod
    def _normalise_modes(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("modes must not be empty")
        allowed = {"V", "VL", "VGL"}
        normalised: List[str] = []
        for raw in value:
            mode = (raw or "").strip().upper()
            if mode not in allowed:
                raise ValueError(f"unsupported mode '{raw}'")
            if mode not in normalised:
                normalised.append(mode)
        return normalised


def clamp_one_line(text: str, max_chars: int) -> str:
    """Utility used for inline fallbacks where LLM helpers are not involved."""

    cleaned = " ".join(text.split())
    return cleaned[:max_chars]


@contextmanager
def timeit(target: Dict[str, int], key: str) -> None:
    start = time.perf_counter()
    try:
        yield
    finally:
        target[key] = int((time.perf_counter() - start) * 1000)


def _get_vlm(request: Request) -> VLMRunner:
    runner: VLMRunner | None = getattr(request.app.state, "vlm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="VLM runner unavailable")
    return runner


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    if not cleaned:
        cleaned = uuid4().hex[:12]
    return cleaned[:48]


def _resolve_identifiers(payload: AnalyzeReq, image_path: Optional[str]) -> Dict[str, str]:
    seed = (
        payload.idempotency_key
        or payload.case_id
        or (Path(image_path).stem if image_path else None)
        or uuid4().hex[:12]
    )
    slug = _slugify(str(seed))
    case_id = payload.case_id or f"CASE_{slug.upper()}"
    image_id = f"IMG_{slug.upper()}"
    return {"case_id": case_id, "image_id": image_id}


def _read_image_bytes(payload: AnalyzeReq) -> tuple[bytes, Optional[str]]:
    if payload.image_b64:
        try:
            return base64.b64decode(payload.image_b64), None
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=422, detail="invalid base64 image payload")
    if payload.file_path:
        path = Path(payload.file_path).expanduser()
        if not path.exists():
            raise HTTPException(status_code=422, detail="file_path does not exist")
        try:
            return path.read_bytes(), str(path)
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"failed to read file: {exc}") from exc
    raise HTTPException(status_code=422, detail="either image_b64 or file_path is required")


def _normalise_vlm_output(
    raw: Dict[str, Any],
    identifiers: Dict[str, str],
    image_path: Optional[str],
) -> Dict[str, Any]:
    caption = str(raw.get("output") or "").strip()
    if not caption:
        raise HTTPException(status_code=502, detail="vlm returned empty caption")

    report_id = raw.get("report_id") or f"rep_{identifiers['image_id'].lower()}"
    report_model = raw.get("model") or raw.get("model_name")
    now_iso = datetime.now(timezone.utc).isoformat()

    findings_raw = raw.get("findings")
    if not isinstance(findings_raw, list):
        findings_raw = []

    normalized_findings: List[Dict[str, Any]] = []
    for idx, item in enumerate(findings_raw):
        if not isinstance(item, dict):
            continue
        finding_id = item.get("id") or f"f_{identifiers['image_id'].lower()}_{idx:02d}"
        normalized_findings.append(
            {
                "id": str(finding_id),
                "type": item.get("type"),
                "location": item.get("location"),
                "size_cm": item.get("size_cm"),
                "conf": item.get("conf"),
            }
        )

    normalized = {
        "case_id": identifiers["case_id"],
        "image": {
            "id": identifiers["image_id"],
            "path": image_path,
            "modality": raw.get("modality"),
        },
        "report": {
            "id": report_id,
            "text": caption,
            "model": report_model,
            "conf": raw.get("confidence"),
            "ts": now_iso,
        },
        "findings": normalized_findings,
    }
    return normalized


async def _ensure_dependencies(request: Request) -> None:
    async with httpx.AsyncClient(app=request.app, base_url="http://internal") as client:
        for path, label in [
            ("/health/llm", "llm"),
            ("/health/vlm", "vlm"),
            ("/health/neo4j", "neo4j"),
        ]:
            try:
                response = await client.get(path)
            except httpx.HTTPError:
                raise HTTPException(status_code=503, detail={"ok": False, "where": label})
            if response.status_code != 200:
                raise HTTPException(status_code=503, detail={"ok": False, "where": label})
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("ok"):
                raise HTTPException(status_code=503, detail={"ok": False, "where": label})


@router.post("/analyze")
async def analyze(
    payload: AnalyzeReq,
    request: Request,
    sync: bool = Query(True, description="Synchronous execution toggle"),
    llm: LLMRunner = Depends(get_llm),
    vlm: VLMRunner = Depends(_get_vlm),
) -> Dict[str, Any]:
    if not sync:
        raise HTTPException(status_code=400, detail="async execution is not supported")

    await _ensure_dependencies(request)

    timings: Dict[str, int] = {
        "vlm_ms": 0,
        "upsert_ms": 0,
        "context_ms": 0,
        "llm_v_ms": 0,
        "llm_vl_ms": 0,
        "llm_vgl_ms": 0,
    }
    errors: List[Dict[str, str]] = []
    current_stage = "init"
    graph_repo: Optional[GraphRepo] = None
    context_builder: Optional[GraphContextBuilder] = None

    try:
        current_stage = "image_load"
        try:
            image_bytes, image_path = _read_image_bytes(payload)
        except HTTPException:
            raise

        identifiers = _resolve_identifiers(payload, image_path)
        case_id = identifiers["case_id"]
        image_id = identifiers["image_id"]

        current_stage = "vlm"
        with timeit(timings, "vlm_ms"):
            vlm_result = await vlm.generate(image_bytes, CAPTION_PROMPT, task=VLMRunner.Task.CAPTION)

        normalized = _normalise_vlm_output(vlm_result, identifiers, image_path)

        graph_repo = GraphRepo.from_env()
        context_builder = GraphContextBuilder(graph_repo)

        current_stage = "upsert"
        graph_payload = {
            "case_id": case_id,
            "image": normalized["image"],
            "report": normalized["report"],
            "findings": normalized["findings"],
            "idempotency_key": payload.idempotency_key,
        }
        with timeit(timings, "upsert_ms"):
            graph_repo.upsert_case(graph_payload)

        current_stage = "context"
        with timeit(timings, "context_ms"):
            context_bundle = context_builder.build_bundle(
                id=image_id,
                k=payload.k,
                max_chars=GRAPH_TRIPLE_CHAR_CAP,
            )

        results: Dict[str, Dict[str, Any]] = {}

        if "V" in payload.modes:
            current_stage = "llm_v"
            start = time.perf_counter()
            try:
                v_result = run_v_mode(normalized, payload.max_chars)
            except LLMInputError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            timings["llm_v_ms"] = int((time.perf_counter() - start) * 1000)
            v_result.setdefault("latency_ms", timings["llm_v_ms"])
            v_result["text"] = clamp_one_line(v_result.get("text", ""), payload.max_chars)
            results["V"] = v_result

        if "VL" in payload.modes:
            current_stage = "llm_vl"
            start = time.perf_counter()
            try:
                vl_result = await run_vl_mode(llm, normalized, payload.max_chars)
            except LLMInputError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            timings["llm_vl_ms"] = int((time.perf_counter() - start) * 1000)
            vl_result.setdefault("latency_ms", timings["llm_vl_ms"])
            results["VL"] = vl_result

        if "VGL" in payload.modes:
            current_stage = "llm_vgl"
            start = time.perf_counter()
            try:
                vgl_result = await run_vgl_mode(
                    llm,
                    image_id,
                    context_bundle.get("triples", ""),
                    payload.max_chars,
                    payload.fallback_to_vl,
                    normalized,
                )
            except LLMInputError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            timings["llm_vgl_ms"] = int((time.perf_counter() - start) * 1000)
            vgl_result.setdefault("latency_ms", timings["llm_vgl_ms"])
            if "degraded" not in vgl_result:
                vgl_result["degraded"] = False
            results["VGL"] = vgl_result

        response = {
            "ok": True,
            "case_id": case_id,
            "image_id": image_id,
            "graph_context": context_bundle,
            "results": results,
            "timings": timings,
            "errors": errors,
        }
        return response

    except HTTPException:
        raise
    except LLMInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        errors.append({"stage": current_stage, "msg": str(exc)})
        detail = {"ok": False, "errors": errors}
        raise HTTPException(status_code=500, detail=detail) from exc
    finally:
        if context_builder is not None:
            context_builder.close()
        if graph_repo is not None:
            graph_repo.close()
