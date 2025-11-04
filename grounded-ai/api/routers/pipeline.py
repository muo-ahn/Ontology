"""One-shot orchestration endpoint that chains vLM → graph → LLM."""

from __future__ import annotations
modality: Optional[str] = None,
import base64
import binascii
import logging
import os
import re
import time
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from models.pipeline import AnalyzeResp
from services.context_pack import GraphContextBuilder
from services.dummy_registry import DummyImageRegistry, LookupResult
from services.dedup import dedup_findings
from services.graph_repo import GraphRepo
from services.llm_runner import LLMRunner
from services.normalizer import normalize_from_vlm
from services.similarity import compute_similarity_scores
from services.vlm_runner import VLMRunner

from .llm import (
    BANNED_BY_MODALITY,
    LLMInputError,
    get_llm,
    modality_penalty,
    run_v_mode,
    run_vgl_mode,
    run_vl_mode,
)

GRAPH_TRIPLE_CHAR_CAP = 1800
CONSENSUS_AGREEMENT_THRESHOLD = 0.6
CONSENSUS_HIGH_CONFIDENCE_THRESHOLD = 0.8
CONSENSUS_MODE_PRIORITY = ("VGL", "VL", "V")

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

logger = logging.getLogger(__name__)


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
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Optional overrides for similarity/context scoring")
    k_paths: Optional[int] = Field(default=None, ge=0, le=10)
    alpha_finding: Optional[float] = Field(default=None)
    beta_report: Optional[float] = Field(default=None)
    similarity_threshold: Optional[float] = Field(default=None)

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

    @field_validator("similarity_threshold", mode="after")
    @classmethod
    def _clamp_similarity_threshold(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0")
        return float(value)


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


def compute_consensus(
    results: Dict[str, Dict[str, Any]],
    modality: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
    min_agree: Optional[float] = None,
) -> Dict[str, Any]:
    available: Dict[str, Dict[str, Any]] = {}
    weight_map = {k: float(v) for k, v in (weights or {}).items()}
    fallback_threshold = min_agree if min_agree is not None else CONSENSUS_AGREEMENT_THRESHOLD
    modality_key = (modality or "").upper()
    penalised_modes: set[str] = set()
    for mode, payload in results.items():
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            lowered = text.lower()
            banned_terms = BANNED_BY_MODALITY.get(modality_key, []) if modality_key else []
            offending_terms = [term for term in banned_terms if term in lowered]
            penalty = modality_penalty(text, modality_key)
            if penalty < 0:
                penalised_modes.add(mode)
            base_weight = weight_map.get(mode, 1.0)
            effective_weight = max(base_weight + penalty, 0.0)
            available[mode] = {
                "text": text,
                "normalised": _normalise_for_consensus(text),
                "latency_ms": payload.get("latency_ms"),
                "degraded": payload.get("degraded"),
                "penalty": penalty,
                "penalty_terms": offending_terms,
                "effective_weight": effective_weight,
                "base_weight": base_weight,
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
    best_pair_weight = 1.0
    best_weighted_score = -1.0
    best_raw_score = 0.0
    best_pair_penalty_modes: tuple[str, ...] = ()
    for (mode_a, data_a), (mode_b, data_b) in combinations(available.items(), 2):
        score = _jaccard_similarity(data_a["normalised"], data_b["normalised"])
        weight_a = data_a.get("effective_weight", weight_map.get(mode_a, 1.0))
        weight_b = data_b.get("effective_weight", weight_map.get(mode_b, 1.0))
        pair_weight = max(weight_a + weight_b, 0.0) / 2.0
        penalty_adjustment = (
            min(data_a.get("penalty", 0.0), 0.0) + min(data_b.get("penalty", 0.0), 0.0)
        ) / 2.0
        adjusted_score = max(score + penalty_adjustment, 0.0)
        weighted_score = adjusted_score * pair_weight
        if weighted_score > best_weighted_score:
            best_weighted_score = weighted_score
            best_pair = (mode_a, mode_b)
            best_raw_score = adjusted_score
            best_pair_weight = pair_weight
            best_pair_penalty_modes = tuple(
                sorted({candidate for candidate in (mode_a, mode_b) if available[candidate]["penalty"] < 0})
            )

    agreement_score = max(best_raw_score, 0.0)
    supporting_modes: List[str] = []
    fallback_used = False
    if best_pair:
        effective_threshold = CONSENSUS_AGREEMENT_THRESHOLD
        if agreement_score >= effective_threshold:
            supporting_modes = sorted(
                best_pair,
                key=lambda mode: CONSENSUS_MODE_PRIORITY.index(mode)
                if mode in CONSENSUS_MODE_PRIORITY
                else len(CONSENSUS_MODE_PRIORITY),
            )
        elif (
            agreement_score >= fallback_threshold
            and best_pair_weight > 1.0
        ):
            supporting_modes = sorted(
                best_pair,
                key=lambda mode: CONSENSUS_MODE_PRIORITY.index(mode)
                if mode in CONSENSUS_MODE_PRIORITY
                else len(CONSENSUS_MODE_PRIORITY),
            )
            fallback_used = True

    penalty_note: Optional[str] = None
    if supporting_modes:
        conflicted = [mode for mode in supporting_modes if available[mode].get("penalty", 0.0) < 0]
        if conflicted:
            penalty_note = "modality conflict: " + ", ".join(sorted(conflicted))
            supporting_modes = [mode for mode in supporting_modes if available[mode]["penalty"] >= 0]
    elif best_pair_penalty_modes:
        penalty_note = "modality conflict: " + ", ".join(best_pair_penalty_modes)

    disagreed_modes = sorted(set(available.keys()) - set(supporting_modes))

    notes: Optional[str] = None
    if supporting_modes:
        preferred = _preferred_mode(supporting_modes) or supporting_modes[0]
        consensus_text = available[preferred]["text"]
        if agreement_score >= CONSENSUS_HIGH_CONFIDENCE_THRESHOLD:
            confidence = "high"
        elif fallback_used:
            confidence = "medium"
        else:
            confidence = "medium"
        status = "agree"
        if fallback_used:
            notes = "weighted agreement favouring grounded evidence"
        else:
            notes = "agreement across requested modes"
    else:
        preferred = _preferred_mode(list(available.keys())) or next(iter(available.keys()))
        consensus_text = available[preferred]["text"]
        confidence = "low"
        status = "disagree"
        supporting_modes = [preferred] if preferred else []
        notes = "outputs diverged across modes"
        if available.get(preferred, {}).get("penalty", 0.0) < 0:
            terms = available.get(preferred, {}).get("penalty_terms", [])
            detail_terms = ", ".join(sorted(set(terms))) if terms else "unexpected content"
            penalty_detail = f"penalised terms: {detail_terms}"
            penalty_note = f"{penalty_note} | {penalty_detail}" if penalty_note else penalty_detail
            confidence = "very_low"

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
    all_notes: List[str] = []
    if notes:
        all_notes.append(notes)
    if penalty_note:
        all_notes.append(penalty_note)
    if penalised_modes and status != "disagree" and not penalty_note:
        all_notes.append("penalty applied for modality conflict")
    if all_notes:
        consensus_payload["notes"] = " | ".join(all_notes)

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


def _derive_image_id_from_path(path: Optional[str]) -> Tuple[Optional[str], Optional[LookupResult]]:
    if not path:
        return None, None

    lookup = DummyImageRegistry.resolve_by_path(path)
    if lookup:
        return lookup.image_id, lookup

    stem = Path(path).stem
    alnum = re.sub(r"[^A-Za-z0-9]+", "", stem)
    if not alnum:
        return None, None
    return alnum.upper()[:48], None


def _resolve_seed_storage_uri(
    file_path: Optional[str],
    image_id: Optional[str],
    preferred: Optional[str] = None,
) -> Optional[str]:
    if preferred:
        candidate = preferred.strip()
        if candidate:
            return candidate
    if not file_path:
        return None
    path = Path(file_path)
    raw = str(path)
    if raw.startswith("/mnt/data/medical_dummy/") or raw.startswith("/data/dummy/"):
        return raw

    suffix = path.suffix.lower()
    if not suffix:
        suffix = ".png"
    stem = path.stem
    normalized_id = (image_id or "").strip().upper()
    stem_upper = stem.upper()

    if re.match(r"^IMG_\d+$", normalized_id):
        return f"/mnt/data/medical_dummy/images/{normalized_id.lower()}{suffix}"
    if re.match(r"^IMG_\d+$", stem_upper):
        return f"/mnt/data/medical_dummy/images/{stem.lower()}{suffix}"

    if re.match(r"^IMG\d+$", normalized_id):
        return f"/data/dummy/{normalized_id}{suffix}"
    if re.match(r"^IMG\d+$", stem_upper):
        return f"/data/dummy/{stem_upper}{suffix}"

    if re.match(r"^(CT|US|XR)\d+$", normalized_id):
        return f"/data/dummy/{normalized_id}{suffix}"
    if re.match(r"^(CT|US|XR)\d+$", stem_upper):
        return f"/data/dummy/{stem_upper}{suffix}"

    if stem.lower().startswith("img_"):
        return f"/mnt/data/medical_dummy/images/{stem.lower()}{suffix}"

    return raw


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
    param_overrides: Dict[str, Any] = dict(payload.parameters or {})

    def _resolve_int_param(
        primary: Optional[int],
        key: str,
        default: int,
        *,
        ge: Optional[int] = None,
        le: Optional[int] = None,
    ) -> int:
        candidate = primary if primary is not None else param_overrides.get(key)
        if candidate is None:
            return default
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{key} must be an integer")
        if ge is not None and value < ge:
            raise HTTPException(status_code=422, detail=f"{key} must be ≥ {ge}")
        if le is not None and value > le:
            raise HTTPException(status_code=422, detail=f"{key} must be ≤ {le}")
        return value

    def _resolve_float_param(
        primary: Optional[float],
        key: str,
        default: Optional[float],
        *,
        ge: Optional[float] = None,
        le: Optional[float] = None,
    ) -> Optional[float]:
        candidate = primary if primary is not None else param_overrides.get(key)
        if candidate is None:
            return default
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{key} must be a number")
        if ge is not None and value < ge:
            raise HTTPException(status_code=422, detail=f"{key} must be ≥ {ge}")
        if le is not None and value > le:
            raise HTTPException(status_code=422, detail=f"{key} must be ≤ {le}")
        return value

    def _resolve_confidence_level(score: float, path_triples: int) -> str:
        if score >= 0.7 and path_triples >= 3:
            return "high"
        if score >= 0.5 and path_triples >= 3:
            return "medium"
        return "low"

    resolved_k_paths = _resolve_int_param(payload.k_paths, "k_paths", payload.k, ge=0, le=10)

    slot_overrides: Dict[str, int] = {}
    slot_param_map = {
        "k_findings": "findings",
        "k_reports": "reports",
        "k_similarity": "similarity",
    }
    for param_name, slot_name in slot_param_map.items():
        if param_name not in param_overrides:
            continue
        raw_value = param_overrides[param_name]
        try:
            slot_value = int(raw_value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{param_name} must be an integer")
        if slot_value < 0:
            raise HTTPException(status_code=422, detail=f"{param_name} must be ≥ 0")
        slot_overrides[slot_name] = slot_value
    alpha_param = _resolve_float_param(payload.alpha_finding, "alpha_finding", None)
    beta_param = _resolve_float_param(payload.beta_report, "beta_report", None)
    similarity_threshold = _resolve_float_param(payload.similarity_threshold, "similarity_threshold", 0.5, ge=0.0, le=1.0)
    similar_seed_images: List[Dict[str, Any]] = []
    similarity_edges_created = 0
    similarity_candidates_debug = 0

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
        lookup_result: Optional[LookupResult] = None
        lookup_source: Optional[str] = None
        image_id_source = "normalizer"
        resolved_path = payload.file_path or image_path

        if payload.image_id:
            try:
                normalized_image_id = DummyImageRegistry.normalise_id(payload.image_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="image_id must not be blank") from exc
            image_id_source = "payload"
        else:
            derived_image_id, lookup_candidate = _derive_image_id_from_path(resolved_path)
            if derived_image_id:
                normalized_image_id = derived_image_id
                if lookup_candidate:
                    lookup_result = lookup_candidate
                    lookup_source = lookup_candidate.source
                    image_id_source = "dummy_lookup"
                else:
                    image_id_source = "file_path"

        if not normalized_image_id:
            raise HTTPException(status_code=502, detail="unable to derive image identifier")

        try:
            normalized_image_id = DummyImageRegistry.normalise_id(str(normalized_image_id))
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="unable to derive image identifier") from exc

        if not lookup_result:
            lookup_result = DummyImageRegistry.resolve_by_id(normalized_image_id)
            if lookup_result:
                lookup_source = lookup_result.source
                if image_id_source != "payload":
                    image_id_source = "dummy_lookup"

        image_id = normalized_image_id
        case_id = _resolve_case_id(payload, image_path, image_id)
        final_image_path = image_path or payload.file_path or normalized_image.get("path")
        lookup_storage_uri = lookup_result.storage_uri if lookup_result else None
        storage_uri = _resolve_seed_storage_uri(resolved_path, normalized_image_id, preferred=lookup_storage_uri)
        if not storage_uri:
            storage_uri = normalized_image.get("storage_uri")
        if not storage_uri and final_image_path:
            storage_uri = _resolve_seed_storage_uri(final_image_path, normalized_image_id)
            if not storage_uri:
                storage_uri = str(final_image_path)
        if storage_uri and isinstance(storage_uri, str):
            storage_uri = storage_uri.strip() or None

        if lookup_result and lookup_result.modality and not normalized_image.get("modality"):
            normalized_image["modality"] = lookup_result.modality

        storage_uri_key = os.path.basename(storage_uri) if storage_uri else None
        if not storage_uri_key and resolved_path:
            storage_uri_key = os.path.basename(str(resolved_path))
        if storage_uri_key:
            storage_uri_key = storage_uri_key.strip() or None

        logger.info(
            "pipeline.normalize.image_id",
            extra={
                "case_id": case_id,
                "image_id": normalized_image_id,
                "image_id_source": image_id_source,
                "image_path": resolved_path,
                "storage_uri_key": storage_uri_key,
                "dummy_lookup_hit": bool(lookup_result),
                "dummy_lookup_source": lookup_source,
            },
        )

        normalized_image.update({
            "image_id": image_id,
            "path": final_image_path,
        })
        if storage_uri:
            normalized_image["storage_uri"] = storage_uri
        if storage_uri_key:
            normalized_image["storage_uri_key"] = storage_uri_key
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
            debug_blob["norm_image_id"] = image_id
            debug_blob["norm_image_id_source"] = image_id_source
            debug_blob["storage_uri"] = storage_uri
            debug_blob["dummy_lookup_hit"] = bool(lookup_result)
            if lookup_source:
                debug_blob["dummy_lookup_source"] = lookup_source
            if not lookup_result and image_id_source != "payload":
                debug_blob["norm_image_id_warning"] = "dummy_lookup_miss"

        graph_repo = GraphRepo.from_env()
        context_builder = GraphContextBuilder(graph_repo)

        logger.info(
            "pipeline.diag.pre_graph",
            extra={
                "case_id": case_id,
                "image_id": image_id,
                "modality": normalized_image.get("modality"),
                "storage_uri": normalized_image.get("storage_uri"),
                "storage_uri_key": normalized_image.get("storage_uri_key"),
            },
        )

        current_stage = "upsert"
        image_payload = {
            "image_id": image_id,
            "modality": normalized_image.get("modality"),
            "storage_uri": normalized_image.get("storage_uri"),
            "path": normalized_image.get("path"),
        }
        graph_payload = {
            "case_id": case_id,
            "image": image_payload,
            "report": normalized["report"],
            "findings": normalized_findings,
            "idempotency_key": payload.idempotency_key,
        }
        with timeit(timings, "upsert_ms"):
            upsert_receipt_raw = graph_repo.upsert_case(graph_payload)
        upsert_receipt = dict(upsert_receipt_raw or {})
        resolved_image_id = upsert_receipt.get("image_id")
        if resolved_image_id:
            image_id = resolved_image_id
            normalized_image["image_id"] = resolved_image_id
            normalized["image"]["image_id"] = resolved_image_id
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

        if graph_repo is not None:
            current_stage = "similarity"
            try:
                candidates = graph_repo.fetch_similarity_candidates(image_id)
                similarity_candidates_debug = len(candidates)
                new_image_payload = {
                    "modality": normalized_image.get("modality"),
                    "findings": normalized_findings,
                }
                edges_payload, summary_payload = compute_similarity_scores(
                    new_image=new_image_payload,
                    candidates=candidates,
                    threshold=float(similarity_threshold) if similarity_threshold is not None else 0.5,
                    top_k=10,
                )
                similar_seed_images = summary_payload
                similarity_edges_created = graph_repo.sync_similarity_edges(image_id, edges_payload)
            except Exception as exc:
                errors.append({"stage": "similarity", "msg": str(exc)})

        current_stage = "context"
        with timeit(timings, "context_ms"):
            context_bundle = context_builder.build_bundle(
                image_id=image_id,
                k=resolved_k_paths,
                max_chars=GRAPH_TRIPLE_CHAR_CAP,
                alpha_finding=alpha_param,
                beta_report=beta_param,
                k_slots=slot_overrides or None,
            )

        no_graph_evidence = False
        facts: Any = {}
        paths: Any = []
        findings_list: List[Dict[str, Any]] = []
        paths_list: List[Dict[str, Any]] = []
        ctx_paths_total = 0
        try:
            facts = (context_bundle.get("facts") or {})
            paths = (context_bundle.get("paths") or [])
            
            if (isinstance(facts.get("findings"), list) and not facts["findings"]) and not paths:
                no_graph_evidence = (len(finding_ids) == 0) and (len(facts.get("findings") or []) == 0) and (len(paths) == 0)

        except Exception:
            pass

        if isinstance(facts, dict):
            findings_list = list(facts.get("findings") or [])
        if isinstance(paths, list):
            paths_list = list(paths)
        ctx_paths_total = sum(len(path.get("triples") or []) for path in paths_list)

        if debug_enabled:
            debug_blob.update({
                "stage": "context",
                "context_summary": context_bundle.get("summary"),
                "context_findings_len": len(findings_list),
                "context_findings_head": findings_list[:2],
                "context_paths_len": len(paths_list),
                "context_paths_head": paths_list[:2],
                "context_paths_triple_total": ctx_paths_total,
                "context_slot_limits": context_bundle.get("slot_limits"),
                "similar_seed_images": similar_seed_images,
                "similarity_edges_created": similarity_edges_created,
                "similarity_threshold": similarity_threshold,
                "similarity_candidates_considered": similarity_candidates_debug,
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

        has_paths = (debug_blob.get("context_paths_len", 0) or 0) > 0
        weights = {"V": 1.0, "VL": 1.2, "VGL": 1.0}
        if has_paths:
            weights["VGL"] = 1.8

        consensus = compute_consensus(results, weights=weights, min_agree=0.35)
        results["consensus"] = consensus
        debug_blob["consensus"] = consensus

        # --- Post-consensus safety filter ---
        ORGAN_KEYWORDS = {
            "brain": ["brain", "cerebral", "stroke", "infarct"],
            "liver": ["liver", "hepatic"],
            "lung": ["lung", "pulmonary"],
            "heart": ["heart", "cardiac"],
        }

        def _infer_expected_from_path(file_path: str):
            path_lower = file_path.lower()
            if "brain" in path_lower or "head" in path_lower:
                return "brain"
            if "liver" in path_lower or "abdomen" in path_lower:
                return "liver"
            if "chest" in path_lower:
                return "lung"
            return None

        expected_organ = _infer_expected_from_path(payload.file_path)

        if expected_organ:
            offending = []
            for organ, kws in ORGAN_KEYWORDS.items():
                if organ != expected_organ and any(kw in consensus["text"].lower() for kw in kws):
                    offending.append(organ)
            if offending:
                consensus["status"] = "disagree"
                consensus["confidence"] = "very_low"
                consensus["notes"] += f" | Guard: {offending} terms inconsistent with expected {expected_organ}"
                consensus["presented_text"] = (
                    "낮은 확신: 장기 불일치 가능성이 있어 단정이 어렵습니다."
                )

        results["similar_seed_images"] = similar_seed_images
        agreement_score = float(consensus.get("agreement_score") or 0.0)
        confidence_level = _resolve_confidence_level(agreement_score, ctx_paths_total)
        consensus_notes = consensus.get("notes") or ""
        evaluation_consensus = {
            "text": consensus.get("text") or "",
            "status": consensus.get("status") or "",
            "notes": consensus_notes,
        }
        if consensus.get("supporting_modes"):
            evaluation_consensus["supporting_modes"] = consensus.get("supporting_modes")
        if consensus.get("disagreed_modes"):
            evaluation_consensus["disagreed_modes"] = consensus.get("disagreed_modes")

        evaluation_payload = {
            "image_id": image_id,
            "similar_seed_images": similar_seed_images,
            "edges_created": similarity_edges_created,
            "ctx_paths_len": ctx_paths_total,
            "agreement_score": round(agreement_score, 3),
            "confidence": confidence_level,
            "context_paths": paths_list,
            "consensus": evaluation_consensus,
        }

        if debug_enabled:
            debug_blob["evaluation"] = evaluation_payload

        response = {
            "ok": True,
            "case_id": case_id,
            "image_id": image_id,
            "graph_context": context_bundle,
            "results": results,
            "timings": timings,
            "errors": errors,
            "debug": debug_blob if debug_enabled else {},
            "evaluation": evaluation_payload,
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
