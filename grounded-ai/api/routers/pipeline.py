"""One-shot orchestration endpoint that chains vLM → graph → LLM."""

from __future__ import annotations
modality: Optional[str] = None,
import base64
import binascii
import logging
import os
import time
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional
import hashlib

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from models.pipeline import AnalyzeResp
from services.context_pack import GraphContextBuilder
from services.dummy_registry import DummyFindingRegistry
from services.dedup import dedup_findings
from services.graph_repo import GraphRepo
from services.image_identity import ImageIdentityError, identify_image
from services.llm_runner import LLMRunner
from services.normalizer import normalize_from_vlm
from services.consensus import compute_consensus, normalise_for_consensus, _jaccard_similarity
from services.similarity import compute_similarity_scores
from services.vlm_runner import VLMRunner

from .llm import (
    LLMInputError,
    get_llm,
    run_v_mode,
    run_vgl_mode,
    run_vl_mode,
)

GRAPH_TRIPLE_CHAR_CAP = 1800

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

logger = logging.getLogger(__name__)


def _replace_image_tokens(text: Optional[str], image_id: Optional[str]) -> Optional[str]:
    if not isinstance(text, str) or not image_id:
        return text
    result = text
    for token in ["(IMAGE_ID)", "IMAGE_ID"]:
        result = result.replace(token, image_id)
    return result


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


def _graph_paths_strength(path_count: int, triple_total: int) -> float:
    if path_count <= 0 or triple_total <= 0:
        return 0.0
    coverage = min(1.0, path_count / 3.0)
    depth = min(1.0, triple_total / 6.0)
    return round(min(1.0, (coverage * 0.4) + (depth * 0.6)), 3)


def _fallback_paths_from_findings(
    image_id: Optional[str],
    findings: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not findings:
        return []
    budget = max(int(limit), 1)
    token = str(image_id or "").strip() or "UNKNOWN"
    fallback_paths: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        fid = str(
            finding.get("id")
            or finding.get("finding_id")
            or finding.get("uid")
            or f"FALLBACK_{len(fallback_paths) + 1}"
        )
        label = str(finding.get("type") or finding.get("label") or f"Finding[{fid}]")
        location = str(finding.get("location") or "").strip()
        triples = [f"Image[{token}] -HAS_FINDING-> Finding[{fid}]"]
        if location:
            triples.append(f"Finding[{fid}] -LOCATED_IN-> Anatomy[{location}]")
        score_raw = finding.get("conf")
        try:
            score = float(score_raw) if score_raw is not None else 0.5
        except (TypeError, ValueError):
            score = 0.5
        fallback_paths.append({
            "slot": "findings",
            "label": label,
            "triples": triples,
            "score": score,
        })
        if len(fallback_paths) >= budget:
            break
    return fallback_paths


def _ensure_findings_slot_allocation(bundle: Dict[str, Any], minimum: int) -> None:
    if minimum <= 0:
        return
    slot_limits = bundle.get("slot_limits")
    if not isinstance(slot_limits, dict):
        slot_limits = {"findings": minimum, "reports": 0, "similarity": 0}
        bundle["slot_limits"] = slot_limits
    else:
        slot_limits["findings"] = max(int(slot_limits.get("findings", 0)), minimum)
    slot_meta = bundle.get("slot_meta")
    try:
        allocated_total = sum(max(int(slot_limits.get(key, 0)), 0) for key in ("findings", "reports", "similarity"))
    except Exception:
        allocated_total = minimum
    if isinstance(slot_meta, dict):
        slot_meta["allocated_total"] = max(int(slot_meta.get("allocated_total", 0)), allocated_total)
    else:
        bundle["slot_meta"] = {"allocated_total": allocated_total, "slot_source": "auto"}


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


def _compute_cache_seed(payload: AnalyzeReq) -> Optional[str]:
    if payload.file_path:
        return os.path.abspath(payload.file_path)
    if payload.image_id:
        candidate = payload.image_id.strip()
        if candidate:
            return candidate
    if payload.image_b64:
        digest = hashlib.sha1(payload.image_b64.encode("utf-8")).hexdigest()
        return f"b64:{digest}"
    if payload.idempotency_key:
        candidate = payload.idempotency_key.strip()
        if candidate:
            return candidate
    return None


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
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
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
    overall_status: Optional[str] = None
    overall_notes: Optional[str] = None
    graph_degraded = False
    graph_repo: Optional[GraphRepo] = None
    context_builder: Optional[GraphContextBuilder] = None
    debug_blob: Dict[str, Any] = {"stage": "init"}
    param_overrides: Dict[str, Any] = dict(payload.parameters or {})
    force_dummy_fallback = _is_truthy(param_overrides.get("force_dummy_fallback"))
    normalization_cache_seed = _compute_cache_seed(payload) if debug_enabled else None

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
    vgl_fallback_used = False
    vgl_fallback_reason: Optional[str] = None

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
                force_dummy_fallback=force_dummy_fallback,
                cache_seed=normalization_cache_seed,
                enable_cache=debug_enabled,
            )
        if debug_enabled:
            debug_blob["stage"] = current_stage

        if temp_file:
            try:
                os.unlink(temp_file)
            except OSError:
                pass

        normalized_image = dict(normalized.get("image") or {})
        resolved_path = payload.file_path or image_path
        try:
            identity, normalized_image = identify_image(
                payload=payload,
                normalized_image=normalized_image,
                resolved_path=resolved_path,
                image_path=image_path,
            )
        except ImageIdentityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        image_id = identity.image_id
        case_id = identity.case_id
        image_id_source = identity.image_id_source
        lookup_result = identity.lookup_result
        lookup_source = identity.lookup_source
        storage_uri = identity.storage_uri or normalized_image.get("storage_uri")
        storage_uri_key = identity.storage_uri_key or normalized_image.get("storage_uri_key")
        final_image_path = normalized_image.get("path")

        logger.info(
            "pipeline.normalize.image_id",
            extra={
                "case_id": case_id,
                "image_id": image_id,
                "image_id_source": image_id_source,
                "image_path": resolved_path,
                "storage_uri_key": storage_uri_key,
                "dummy_lookup_hit": bool(lookup_result),
                "dummy_lookup_source": lookup_source,
            },
        )

        normalized["image"] = normalized_image

        normalized_report = dict(normalized.get("report") or {})
        if normalized_report.get("conf") is not None:
            normalized_report["conf"] = float(normalized_report["conf"])
        else:
            normalized_report["conf"] = 0.8
        normalized["report"] = normalized_report

        # Normalize + dedup findings (keep list[dict] invariant)
        normalized_findings = dedup_findings(list(normalized.get("findings") or []))

        seeded_finding_ids: List[str] = []
        seeded_records: List[Dict[str, Any]] = []
        try:
            seeded_stubs = DummyFindingRegistry.resolve(image_id)
        except ValueError:
            seeded_stubs = []
        if seeded_stubs:
            seeded_records = [
                {
                    "id": stub.finding_id,
                    "type": stub.type,
                    "location": stub.location,
                    "size_cm": stub.size_cm,
                    "conf": stub.conf,
                    "source": stub.source,
                }
                for stub in seeded_stubs
            ]

        # Preserve whatever the normalizer decided; we only ever augment this blob to avoid
        # clobbering evidence about seeded findings during later normalization passes.
        fallback_meta = dict(normalized.get("finding_fallback") or {})
        fallback_used = bool(fallback_meta.get("used"))
        fallback_strategy = fallback_meta.get("strategy")
        fallback_registry_hit = bool(fallback_meta.get("registry_hit"))
        fallback_forced = bool(fallback_meta.get("force")) or force_dummy_fallback

        seeded_applied = False
        if (force_dummy_fallback or not normalized_findings) and seeded_records:
            normalized_findings = dedup_findings(seeded_records)
            seeded_applied = True
            fallback_used = True
            fallback_registry_hit = True
            if not fallback_strategy:
                fallback_strategy = "mock_seed"

        normalized["findings"] = normalized_findings

        seeded_finding_ids: List[str] = []
        for finding in normalized_findings:
            fid = finding.get("id")
            if finding.get("source") == "mock_seed" and isinstance(fid, str) and fid not in seeded_finding_ids:
                seeded_finding_ids.append(fid)
        if seeded_applied and not seeded_finding_ids:
            seeded_finding_ids = [
                stub.get("id") for stub in seeded_records if isinstance(stub.get("id"), str)
            ]

        finding_source: Optional[str] = None
        if fallback_used:
            if isinstance(fallback_strategy, str) and fallback_strategy:
                finding_source = fallback_strategy
            elif fallback_registry_hit:
                finding_source = "mock_seed"
            else:
                finding_source = "fallback"
        else:
            finding_source = next(
                (str(finding.get("source")) for finding in normalized_findings if finding.get("source")),
                None,
            )
        if not finding_source and seeded_finding_ids:
            finding_source = "mock_seed"
        elif not finding_source and normalized_findings:
            finding_source = "vlm"

        public_fallback = {
            "used": fallback_used,
            "strategy": finding_source if fallback_used and finding_source else fallback_strategy,
            "registry_hit": fallback_registry_hit,
            "forced": fallback_forced,
            "seeded_ids": list(seeded_finding_ids),
        }
        fallback_meta.update(dict(public_fallback))
        fallback_meta["seeded_ids_head"] = seeded_finding_ids[:3]
        normalized["finding_fallback"] = fallback_meta
        normalized["finding_source"] = finding_source
        provenance_payload = {
            "finding_source": finding_source,
            "seeded_finding_ids": list(seeded_finding_ids),
            "finding_fallback": dict(public_fallback),
        }
        normalized["finding_provenance"] = dict(provenance_payload)

        if fallback_used:
            logger.info(
                "pipeline.fallback.findings",
                extra={
                    "case_id": case_id,
                    "image_id": image_id,
                    "strategy": finding_source or fallback_strategy or "unknown",
                    "registry_hit": fallback_registry_hit,
                    "seeded_ids": seeded_finding_ids[:3],
                    "forced": fallback_forced,
                },
            )

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
            debug_blob["finding_fallback"] = dict(public_fallback, seeded_ids_head=seeded_finding_ids[:3])
            if finding_source:
                debug_blob["finding_source"] = finding_source
            debug_blob["seeded_finding_ids"] = list(seeded_finding_ids)
            debug_blob["finding_provenance"] = dict(provenance_payload)

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
        upsert_missing_ids = len(normalized_findings) > 0 and persisted_f_cnt == 0
        if upsert_missing_ids and debug:
            errors.append({"stage": "upsert", "msg": "normalized findings present but upsert returned no finding_ids"})
        if upsert_missing_ids:
            graph_degraded = True
            overall_status = "degraded"
            overall_notes = "graph upsert failed, fallback used"

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
        findings_list: List[Dict[str, Any]] = []
        paths_list: List[Dict[str, Any]] = []
        ctx_paths_total = 0
        facts: Dict[str, Any] = {}
        try:
            raw_facts = context_bundle.get("facts") if isinstance(context_bundle, dict) else {}
            facts = raw_facts if isinstance(raw_facts, dict) else {}
        except Exception:
            facts = {}

        raw_findings = facts.get("findings") if isinstance(facts, dict) else []
        if isinstance(raw_findings, list):
            findings_list = list(raw_findings)
        elif raw_findings is None:
            findings_list = []
        else:
            findings_list = []

        if graph_degraded and not findings_list and normalized_findings:
            fallback_findings: List[Dict[str, Any]] = []
            for idx, finding in enumerate(normalized_findings, start=1):
                if not isinstance(finding, dict):
                    continue
                fid = str(finding.get("id") or finding.get("finding_id") or f"FALLBACK_{idx}")
                fallback_findings.append({
                    "id": fid,
                    "type": finding.get("type"),
                    "location": finding.get("location"),
                    "size_cm": finding.get("size_cm"),
                    "conf": finding.get("conf"),
                })
            if fallback_findings:
                findings_list = fallback_findings
                facts = dict(facts or {})
                facts.setdefault("image_id", image_id)
                facts["findings"] = fallback_findings
                if isinstance(context_bundle, dict):
                    context_bundle["facts"] = facts

        raw_paths: Any = []
        try:
            raw_paths = context_bundle.get("paths") if isinstance(context_bundle, dict) else []
        except Exception:
            raw_paths = []
        if isinstance(raw_paths, list):
            paths_list = list(raw_paths)
        else:
            paths_list = []

        if not paths_list and findings_list:
            slot_limits = context_bundle.get("slot_limits") if isinstance(context_bundle, dict) else None
            fallback_budget = 0
            if isinstance(slot_limits, dict):
                try:
                    fallback_budget = max(int(slot_limits.get("findings", 0)), 0)
                except (TypeError, ValueError):
                    fallback_budget = 0
            if fallback_budget == 0:
                fallback_budget = min(len(findings_list), 2)
            fallback_paths = _fallback_paths_from_findings(image_id, findings_list, fallback_budget)
            if fallback_paths:
                paths_list = fallback_paths
                if isinstance(context_bundle, dict):
                    context_bundle["paths"] = paths_list
                    _ensure_findings_slot_allocation(context_bundle, len(paths_list))

        if not findings_list and not paths_list and len(finding_ids) == 0:
            no_graph_evidence = True
        elif graph_degraded and not findings_list and normalized_findings:
            findings_list = list(normalized_findings)
            facts = dict(facts or {})
            facts.setdefault("image_id", image_id)
            facts["findings"] = findings_list
            if isinstance(context_bundle, dict):
                context_bundle["facts"] = facts

        ctx_paths_total = sum(len(path.get("triples") or []) for path in paths_list)
        has_paths = len(paths_list) > 0
        graph_paths_strength = _graph_paths_strength(len(paths_list), ctx_paths_total)

        if isinstance(context_bundle, dict):
            context_bundle.setdefault("finding_source", finding_source)
            context_bundle.setdefault("seeded_finding_ids", list(seeded_finding_ids))
            context_bundle.setdefault("finding_fallback", dict(public_fallback))
            context_bundle.setdefault("finding_provenance", dict(provenance_payload))

        if debug_enabled:
            debug_blob.update({
                "stage": "context",
                "context_summary": context_bundle.get("summary"),
                "context_findings_len": len(findings_list),
                "context_findings_head": findings_list[:2],
                "context_paths_len": len(paths_list),
                "context_paths_head": paths_list[:2],
                "context_paths_triple_total": ctx_paths_total,
                "graph_paths_strength": graph_paths_strength,
                "context_slot_limits": context_bundle.get("slot_limits"),
                "similar_seed_images": similar_seed_images,
                "similarity_edges_created": similarity_edges_created,
                "similarity_threshold": similarity_threshold,
                "similarity_candidates_considered": similarity_candidates_debug,
            })
            if graph_degraded:
                debug_blob["graph_degraded"] = True

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
                degraded_marker = vgl_result.get("degraded")
                degraded_mode: Optional[str] = None
                if isinstance(degraded_marker, str):
                    degraded_mode = degraded_marker.strip().upper()
                elif degraded_marker:
                    degraded_mode = "VL"
                if degraded_mode == "VL":
                    vgl_result["degraded"] = "VL"
                    fallback_reason = vgl_result.get("reason") or "graph context empty; fell back to VL"
                    vgl_result.setdefault("reason", fallback_reason)
                    vgl_fallback_used = True
                    vgl_fallback_reason = fallback_reason
                else:
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
                    vgl_fallback_used = True
                    vgl_fallback_reason = "graph_evidence_missing_or_findings_empty"
                else:
                    timings["llm_vgl_ms"] = 0
                    results["VGL"] = {"text": "Graph findings unavailable", "latency_ms": 0, "degraded": False}

        if finding_source and isinstance(results.get("VGL"), dict):
            results["VGL"]["finding_source"] = finding_source
            if seeded_finding_ids:
                results["VGL"]["seeded_finding_ids"] = seeded_finding_ids

        vgl_entry = results.get("VGL")
        if has_paths and isinstance(vgl_entry, dict) and not vgl_entry.get("degraded"):
            vgl_text = vgl_entry.get("text")
            vgl_norm = normalise_for_consensus(vgl_text) if isinstance(vgl_text, str) else ""
            if vgl_norm:
                for mode_name in ("V", "VL"):
                    entry = results.get(mode_name)
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("degraded"):
                        continue
                    mode_text = entry.get("text")
                    if not isinstance(mode_text, str) or not mode_text.strip():
                        entry["degraded"] = "graph_mismatch"
                        entry.setdefault("notes", "mismatch with graph-backed output")
                        continue
                    mode_norm = normalise_for_consensus(mode_text)
                    if not mode_norm or _jaccard_similarity(mode_norm, vgl_norm) < 0.1:
                        entry["degraded"] = "graph_mismatch"
                        entry.setdefault("notes", "mismatch with graph-backed output")

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
            debug_blob.setdefault("context_paths_len", len(paths_list))
            debug_blob.setdefault("context_paths_head", paths_list[:2])

        weights = {"V": 1.0, "VL": 1.2, "VGL": 1.0}
        if has_paths:
            weights["VGL"] = 1.8

        consensus = compute_consensus(
            results,
            weights=weights,
            min_agree=0.35,
            anchor_mode="VGL" if has_paths else None,
            anchor_min_score=0.75,
            structured_findings=findings_list,
            graph_paths_strength=graph_paths_strength,
        )
        results["consensus"] = consensus
        debug_blob["consensus"] = consensus
        if vgl_fallback_used:
            consensus = dict(consensus)
            consensus["status"] = "low_confidence"
            consensus["confidence"] = "very_low"
            fallback_note = "graph evidence missing; fell back to VL"
            if vgl_fallback_reason:
                fallback_note = vgl_fallback_reason.replace("_", " ")
            existing_notes = consensus.get("notes")
            consensus["notes"] = f"{existing_notes} | {fallback_note}" if existing_notes else fallback_note
            consensus.setdefault("presented_text", consensus.get("text") or "")
            results["consensus"] = consensus
            debug_blob["consensus"] = consensus
            results["status"] = "low_confidence"

        for mode in ("V", "VL", "VGL"):
            entry = results.get(mode)
            if isinstance(entry, dict):
                if "text" in entry:
                    entry["text"] = _replace_image_tokens(entry.get("text"), image_id)
                if "presented_text" in entry:
                    entry["presented_text"] = _replace_image_tokens(entry.get("presented_text"), image_id)

        consensus_entry = results.get("consensus")
        if isinstance(consensus_entry, dict):
            if "text" in consensus_entry:
                consensus_entry["text"] = _replace_image_tokens(consensus_entry.get("text"), image_id)
            if "presented_text" in consensus_entry:
                consensus_entry["presented_text"] = _replace_image_tokens(consensus_entry.get("presented_text"), image_id)
            if "notes" in consensus_entry:
                consensus_entry["notes"] = _replace_image_tokens(consensus_entry.get("notes"), image_id)
        if finding_source:
            results["finding_source"] = finding_source
        if seeded_finding_ids:
            results["seeded_finding_ids"] = seeded_finding_ids
        results["finding_fallback"] = dict(public_fallback)
        results["finding_provenance"] = dict(provenance_payload)

        # --- Post-consensus safety filter ---
        ORGAN_KEYWORDS = {
            "brain": ["brain", "cerebral", "stroke", "infarct"],
            "liver": ["liver", "hepatic"],
            "lung": ["lung", "pulmonary"],
            "heart": ["heart", "cardiac"],
        }

        def _infer_expected_from_path(file_path: Optional[str]):
            if not isinstance(file_path, str) or not file_path:
                return None
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

        evaluation_consensus["text"] = _replace_image_tokens(evaluation_consensus.get("text"), image_id) or ""
        evaluation_consensus["notes"] = _replace_image_tokens(evaluation_consensus.get("notes"), image_id) if evaluation_consensus.get("notes") else evaluation_consensus.get("notes")

        evaluation_status = "degraded" if graph_degraded else results.get("consensus", {}).get("status")
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
        evaluation_payload["status"] = evaluation_status
        if graph_degraded and overall_notes:
            evaluation_payload["notes"] = overall_notes
        elif evaluation_consensus.get("notes"):
            evaluation_payload["notes"] = evaluation_consensus.get("notes")
        evaluation_payload["finding_source"] = finding_source
        evaluation_payload["seeded_finding_ids"] = seeded_finding_ids
        evaluation_payload["finding_fallback"] = dict(public_fallback)
        evaluation_payload["finding_provenance"] = dict(provenance_payload)

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
        if overall_status:
            response["status"] = overall_status
        if overall_notes:
            response["notes"] = overall_notes

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
