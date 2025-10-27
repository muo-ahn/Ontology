"""One-shot orchestration endpoint that chains vLM → graph → LLM."""

from __future__ import annotations

import base64
import binascii
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from services.context_pack import GraphContextBuilder
from services.dedup import dedup_findings
from services.graph_repo import GraphRepo
from services.llm_runner import LLMRunner
from services.normalizer import normalize_from_vlm
from services.vlm_runner import VLMRunner

from .llm import LLMInputError, get_llm, run_v_mode, run_vgl_mode, run_vl_mode

GRAPH_TRIPLE_CHAR_CAP = 1800

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class AnalyzeReq(BaseModel):
    case_id: Optional[str] = Field(default=None, description="Existing case identifier")
    image_id: Optional[str] = Field(default=None, description="Optional image identifier")
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


def _resolve_case_id(payload: AnalyzeReq, image_path: Optional[str], image_id: str) -> str:
    if payload.case_id:
        return payload.case_id
    seed = payload.idempotency_key or image_id or (Path(image_path).stem if image_path else None) or uuid4().hex[:12]
    slug = _slugify(str(seed))
    return f"CASE_{slug.upper()}"


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
    debug: bool = Query(False, description="Emit pre/post-upsert diagnostics"),
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

        temp_file: Optional[str] = None
        image_path_for_vlm = image_path
        if image_path_for_vlm is None:
            suffix = Path(payload.file_path or "image.png").suffix or ".png"
            with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(image_bytes)
                temp_file = tmp.name
            image_path_for_vlm = temp_file

        current_stage = "vlm"
        with timeit(timings, "vlm_ms"):
            normalized = await normalize_from_vlm(
                file_path=image_path_for_vlm,
                image_id=payload.image_id,
                vlm_runner=vlm,
            )

        if temp_file:
            try:
                os.unlink(temp_file)
            except OSError:
                pass

        normalized_image = dict(normalized.get("image") or {})
        normalized_image_id = normalized_image.get("image_id")
        if payload.image_id:
            normalized_image_id = payload.image_id
        if not normalized_image_id:
            raise HTTPException(status_code=502, detail="unable to derive image identifier")

        image_id = normalized_image_id
        case_id = _resolve_case_id(payload, image_path, image_id)

        final_image_path = image_path or payload.file_path or normalized_image.get("path")
        normalized_image.update({
            "image_id": image_id,
            "path": final_image_path,
        })
        normalized["image"] = normalized_image

        normalized_report = dict(normalized.get("report") or {})
        if normalized_report.get("conf") is not None:
            normalized_report["conf"] = float(normalized_report["conf"])
        else:
            normalized_report["conf"] = 0.8
        normalized["report"] = normalized_report

        # Normalize + dedup findings (keep list[dict] invariant)
        normalized_findings = dedup_findings(list(normalized.get("findings") or []))
        normalized["findings"] = normalized_findings

        debug_blob: Dict[str, Any] = {}
        if debug:
            debug_blob.update({
                "stage": "pre_upsert",
                "normalized_image": {
                    "image_id": normalized_image.get("image_id"),
                    "path": normalized_image.get("path"),
                    "modality": normalized_image.get("modality"),
                },
                "pre_upsert_findings_len": len(normalized_findings),
                "pre_upsert_findings_head": normalized_findings[:2],
                "pre_upsert_report_conf": normalized_report.get("conf"),
            })

        graph_repo = GraphRepo.from_env()
        context_builder = GraphContextBuilder(graph_repo)

        current_stage = "upsert"
        graph_payload = {
            "case_id": case_id,
            "image": normalized["image"],
            "report": normalized["report"],
            "findings": normalized_findings,
            "idempotency_key": payload.idempotency_key,
        }
        with timeit(timings, "upsert_ms"):
            upsert_receipt = graph_repo.upsert_case(graph_payload)
        if debug:
            debug_blob.update({
                "stage": "post_upsert",
                "upsert_receipt": upsert_receipt,
                "post_upsert_finding_ids": (upsert_receipt or {}).get("finding_ids", [])[:5],
            })

        persisted_f_cnt = 0
        try:
            persisted_f_cnt = len(upsert_receipt.get("finding_ids") or [])
        except Exception:
            pass
        if debug and len(normalized_findings) > 0 and persisted_f_cnt == 0:
            errors.append({"stage": "upsert", "msg": "normalized findings present but upsert returned no finding_ids"})

        current_stage = "context"
        with timeit(timings, "context_ms"):
            context_bundle = context_builder.build_bundle(
                image_id=image_id,
                k=payload.k,
                max_chars=GRAPH_TRIPLE_CHAR_CAP,
            )

        no_graph_evidence = False
        facts: Any = {}
        paths: Any = []
        try:
            facts = context_bundle.get("facts") or {}
            paths = context_bundle.get("paths") or []
            if (isinstance(facts.get("findings"), list) and not facts["findings"]) and not paths:
                no_graph_evidence = True
        except Exception:
            pass
        if debug:
            debug_blob.update({
                "stage": "context",
                "context_summary": context_bundle.get("summary"),
                "context_findings_len": len((facts.get("findings") or []) if isinstance(facts, dict) else []),
                "context_findings_head": (facts.get("findings") or [])[:2] if isinstance(facts, dict) else [],
                "context_paths_len": len(paths) if isinstance(paths, list) else 0,
                "context_paths_head": paths[:2] if isinstance(paths, list) else [],
            })

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

        vl_result: Optional[Dict[str, Any]] = None
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
            if normalized_findings or not no_graph_evidence:
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
            else:
                if payload.fallback_to_vl:
                    if vl_result is None:
                        current_stage = "llm_vl"
                        start = time.perf_counter()
                        try:
                            vl_result = await run_vl_mode(llm, normalized, payload.max_chars)
                        except LLMInputError as exc:
                            raise HTTPException(status_code=422, detail=str(exc)) from exc
                        timings["llm_vl_ms"] = int((time.perf_counter() - start) * 1000)
                        vl_result.setdefault("latency_ms", timings["llm_vl_ms"])
                        results.setdefault("VL", vl_result)
                    timings["llm_vgl_ms"] = 0
                    vgl_payload = {**vl_result, "degraded": "VL"} if isinstance(vl_result, dict) else {"text": "", "latency_ms": 0, "degraded": "VL"}
                    if debug:
                        vgl_payload["reason"] = "graph_evidence_missing_or_findings_empty"
                    results["VGL"] = vgl_payload
                else:
                    timings["llm_vgl_ms"] = 0
                    results["VGL"] = {"text": "Graph findings unavailable", "latency_ms": 0, "degraded": False}

        response = {
            "ok": True,
            "case_id": case_id,
            "image_id": image_id,
            "graph_context": context_bundle,
            "results": results,
            "timings": timings,
            "errors": errors,
        }
        if debug:
            response["debug"] = debug_blob
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
