"""One-shot orchestration endpoint that chains vLM → graph → LLM."""

from __future__ import annotations

import base64
import binascii
import os
import re
import time
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from models.pipeline import AnalyzeResp
from services.context_pack import GraphContextBuilder
from services.dedup import dedup_findings
from services.graph_repo import GraphRepo
from services.llm_runner import LLMRunner
from services.normalizer import normalize_from_vlm
from services.vlm_runner import VLMRunner

from .llm import LLMInputError, get_llm, run_v_mode, run_vgl_mode, run_vl_mode

GRAPH_TRIPLE_CHAR_CAP = 1800
CONSENSUS_AGREEMENT_THRESHOLD = 0.6
CONSENSUS_HIGH_CONFIDENCE_THRESHOLD = 0.8
CONSENSUS_MODE_PRIORITY = ("VGL", "VL", "V")

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


def _normalise_for_consensus(text: str) -> str:
    return " ".join(text.lower().split())


def _jaccard_similarity(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _preferred_mode(modes: List[str]) -> Optional[str]:
    for mode in CONSENSUS_MODE_PRIORITY:
        if mode in modes:
            return mode
    return modes[0] if modes else None


def _build_consensus(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    available: Dict[str, Dict[str, Any]] = {}
    for mode, payload in results.items():
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            available[mode] = {
                "text": text,
                "normalised": _normalise_for_consensus(text),
                "latency_ms": payload.get("latency_ms"),
                "degraded": payload.get("degraded"),
            }

    total_modes = len(available)
    if total_modes == 0:
        return {
            "text": "",
            "status": "empty",
            "supporting_modes": [],
            "disagreed_modes": [],
            "agreement_score": 0.0,
            "confidence": "low",
        }

    if total_modes == 1:
        sole_mode = next(iter(available.keys()))
        data = available[sole_mode]
        return {
            "text": data["text"],
            "status": "single",
            "supporting_modes": [sole_mode],
            "disagreed_modes": [],
            "agreement_score": 1.0,
            "confidence": "medium",
        }

    best_pair: Optional[tuple[str, str]] = None
    best_score = -1.0
    for (mode_a, data_a), (mode_b, data_b) in combinations(available.items(), 2):
        score = _jaccard_similarity(data_a["normalised"], data_b["normalised"])
        if score > best_score:
            best_score = score
            best_pair = (mode_a, mode_b)

    agreement_score = max(best_score, 0.0)
    supporting_modes: List[str] = []
    if best_pair and agreement_score >= CONSENSUS_AGREEMENT_THRESHOLD:
        supporting_modes = sorted(best_pair, key=lambda mode: CONSENSUS_MODE_PRIORITY.index(mode) if mode in CONSENSUS_MODE_PRIORITY else len(CONSENSUS_MODE_PRIORITY))

    disagreed_modes = sorted(set(available.keys()) - set(supporting_modes))

    notes: Optional[str] = None
    if supporting_modes:
        preferred = _preferred_mode(supporting_modes) or supporting_modes[0]
        consensus_text = available[preferred]["text"]
        confidence = "high" if agreement_score >= CONSENSUS_HIGH_CONFIDENCE_THRESHOLD else "medium"
        status = "agree"
        notes = "agreement across requested modes"
    else:
        preferred = _preferred_mode(list(available.keys())) or next(iter(available.keys()))
        consensus_text = available[preferred]["text"]
        confidence = "low"
        status = "disagree"
        supporting_modes = [preferred] if preferred else []
        notes = "outputs diverged across modes"

    degraded_inputs = sorted(mode for mode, data in available.items() if data.get("degraded"))
    presented_text = consensus_text if status != "disagree" else f"낮은 확신: {consensus_text}"

    consensus_payload: Dict[str, Any] = {
        "text": consensus_text,
        "presented_text": presented_text,
        "status": status,
        "supporting_modes": supporting_modes,
        "disagreed_modes": disagreed_modes,
        "agreement_score": round(agreement_score, 3),
        "confidence": confidence,
    }
    if degraded_inputs:
        consensus_payload["degraded_inputs"] = degraded_inputs
    consensus_payload["evaluated_modes"] = sorted(available.keys())
    if notes:
        consensus_payload["notes"] = notes
    return consensus_payload


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


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


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


@router.post(
    "/analyze",
    response_model=AnalyzeResp,
    response_model_exclude_none=False,
)
async def analyze(
    payload: AnalyzeReq,
    request: Request,
    sync: bool = Query(True, description="Synchronous execution toggle"),
    debug: bool | int | str = Query(
        False,
        description="Emit pre/post-upsert diagnostics (truthy values: 1,true,on,yes)",
    ),
    llm: LLMRunner = Depends(get_llm),
    vlm: VLMRunner = Depends(_get_vlm),
) -> AnalyzeResp:
    if not sync:
        raise HTTPException(status_code=400, detail="async execution is not supported")

    await _ensure_dependencies(request)

    debug_enabled = _is_truthy(debug)

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
    debug_blob: Dict[str, Any] = {"stage": "init"}

    try:
        current_stage = "image_load"
        try:
            image_bytes, image_path = _read_image_bytes(payload)
        except HTTPException:
            raise
        if debug_enabled:
            debug_blob["stage"] = current_stage

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
        if debug_enabled:
            debug_blob["stage"] = current_stage

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

        if debug_enabled:
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
            upsert_receipt_raw = graph_repo.upsert_case(graph_payload)
        upsert_receipt = dict(upsert_receipt_raw or {})
        finding_ids = list(upsert_receipt.get("finding_ids") or [])

        if debug_enabled:
            debug_blob.update({
                "stage": "post_upsert",
                "upsert_receipt": upsert_receipt,
                "post_upsert_finding_ids": finding_ids,
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
            facts = (context_bundle.get("facts") or {})
            paths = (context_bundle.get("paths") or [])
            
            if (isinstance(facts.get("findings"), list) and not facts["findings"]) and not paths:
                no_graph_evidence = (len(finding_ids) == 0) and (len(facts.get("findings") or []) == 0) and (len(paths) == 0)

        except Exception:
            pass

        if debug_enabled:
            findings_list = (facts.get("findings") or []) if isinstance(facts, dict) else []
            paths_list = paths if isinstance(paths, list) else []
            debug_blob.update({
                "stage": "context",
                "context_summary": context_bundle.get("summary"),
                "context_findings_len": len(findings_list),
                "context_findings_head": findings_list[:2],
                "context_paths_len": len(paths_list),
                "context_paths_head": paths_list[:2],
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

        if debug_enabled:
            debug_blob.setdefault("normalized_image", {
                "image_id": normalized_image.get("image_id"),
                "path": normalized_image.get("path"),
                "modality": normalized_image.get("modality"),
            })
            debug_blob.setdefault("pre_upsert_findings_len", len(normalized_findings))
            debug_blob.setdefault("pre_upsert_findings_head", normalized_findings[:2])
            debug_blob.setdefault("pre_upsert_report_conf", normalized_report.get("conf"))
            debug_blob.setdefault("upsert_receipt", upsert_receipt)
            debug_blob.setdefault("post_upsert_finding_ids", finding_ids)
            debug_blob.setdefault("context_summary", context_bundle.get("summary"))
            debug_blob.setdefault("context_findings_len", len((facts.get("findings") or []) if isinstance(facts, dict) else []))
            debug_blob.setdefault("context_findings_head", (facts.get("findings") or [])[:2] if isinstance(facts, dict) else [])
            debug_blob.setdefault("context_paths_len", len(paths if isinstance(paths, list) else []))
            debug_blob.setdefault("context_paths_head", (paths if isinstance(paths, list) else [])[:2])

        consensus = _build_consensus(results)
        results["consensus"] = consensus
        if debug_enabled:
            debug_blob["consensus"] = consensus

        response = {
            "ok": True,
            "case_id": case_id,
            "image_id": image_id,
            "graph_context": context_bundle,
            "results": results,
            "timings": timings,
            "errors": errors,
            "debug": debug_blob if debug_enabled else {},
        }

        return AnalyzeResp(**response)

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
