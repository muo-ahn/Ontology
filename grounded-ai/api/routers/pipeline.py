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
from typing import Any, Dict, Iterable, List, Optional
import hashlib

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from models.pipeline import AnalyzeResp
from services.context_pack import GraphContextBuilder
from services.context_orchestrator import ContextLimits, ContextOrchestrator
from services.debug_payload import DebugPayloadBuilder
from services.dummy_registry import DummyFindingRegistry, DummyImageRegistry
from services.dedup import dedup_findings
from services.finding_validation import FindingValidationError, validate_findings_payload
from services.finding_verifier import FindingVerifier
from services.graph_repo import GraphRepo
from services.fallback_meta import FallbackMeta, FallbackMetaError, coerce_fallback_meta, FallbackMetaGuard
from services.image_identity import ImageIdentityError, identify_image
from services.llm_runner import LLMRunner
from services.normalizer import normalize_from_vlm, _normalise_findings
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


def _detect_context_mismatch(paths: List[Dict[str, Any]], triples_text: Optional[str]) -> tuple[bool, Optional[str]]:
    has_paths = bool(paths)
    text = (triples_text or "").lower()
    mentions_no_path = "no path generated" in text
    if has_paths and mentions_no_path:
        return True, "paths_present_but_marked_missing"
    if not has_paths and not mentions_no_path:
        return True, "paths_missing_without_notice"
    return False, None


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
    finding_verifier: Optional[FindingVerifier] = None
    context_builder: Optional[GraphContextBuilder] = None
    debug_builder = DebugPayloadBuilder(enabled=debug_enabled)
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

    def _validate_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            return validate_findings_payload(findings)
        except FindingValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.detail) from exc

    def _has_raw_markers(findings: Iterable[Dict[str, Any]]) -> bool:
        return any(("raw_type" in (finding or {})) or ("raw_location" in (finding or {})) for finding in findings or [])

    def _build_label_events_from_findings(
        findings: Iterable[Dict[str, Any]],
        *,
        default_rule: str = "synthetic",
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for finding in findings or []:
            if not isinstance(finding, dict):
                continue
            finding_id = finding.get("id")
            if not isinstance(finding_id, str) or not finding_id:
                continue
            raw_type = finding.get("raw_type") or finding.get("type")
            canonical_type = finding.get("type")
            type_rule = finding.get("type_rule") or (raw_type and "canonical") or default_rule
            if raw_type:
                events.append(
                    {
                        "finding_id": finding_id,
                        "field": "type",
                        "raw": raw_type,
                        "canonical": canonical_type or raw_type,
                        "rule": type_rule or default_rule,
                    }
                )
            raw_location = finding.get("raw_location") or finding.get("location")
            canonical_location = finding.get("location")
            location_rule = finding.get("location_rule") or (raw_location and "canonical") or default_rule
            if raw_location:
                events.append(
                    {
                        "finding_id": finding_id,
                        "field": "location",
                        "raw": raw_location,
                        "canonical": canonical_location or raw_location,
                        "rule": location_rule or default_rule,
                    }
                )
        return events

    def _safe_preview(value: Any, limit: int = 2) -> Any:
        if isinstance(value, list):
            return value[:limit]
        if isinstance(value, dict):
            preview = dict(value)
            findings = preview.get("findings")
            if isinstance(findings, list):
                preview["findings"] = findings[:limit]
            return preview
        return value

    def _format_preview(value: Any) -> str:
        try:
            import json

            return json.dumps(_safe_preview(value), ensure_ascii=False)
        except Exception:
            return repr(_safe_preview(value))

    def _raise_upsert_mismatch(
        *,
        expected_ids: List[str],
        receipt_ids: List[str],
        verified_ids: List[str],
        errors_acc: List[Dict[str, Any]],
        case_id: Optional[str],
        image_id: Optional[str],
        raw_payload: Dict[str, Any],
        prepared_payload: Dict[str, Any],
    ) -> None:
        error_entry = {
            "stage": "upsert",
            "msg": "finding_upsert_mismatch",
            "expected_ids": sorted(set(expected_ids)),
            "receipt_ids": sorted(set(receipt_ids)),
            "verified_ids": sorted(set(verified_ids)),
        }
        logger.error(
            "pipeline.upsert.mismatch case=%s image=%s expected=%s receipt=%s verified=%s raw=%s prepared=%s",
            case_id,
            image_id,
            error_entry["expected_ids"],
            error_entry["receipt_ids"],
            error_entry["verified_ids"],
            _format_preview(raw_payload),
            _format_preview(prepared_payload),
        )
        errors_acc.append(error_entry)
        raise HTTPException(status_code=500, detail={"ok": False, "errors": errors_acc})

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
        debug_builder.set_stage(current_stage)

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
        debug_builder.set_stage(current_stage)

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
        label_normalization_events: List[Dict[str, Any]] = list(normalized.get("label_normalization") or [])
        normalized_findings = dedup_findings(list(normalized.get("findings") or []))
        normalized_findings = _validate_findings(normalized_findings)

        if not label_normalization_events and normalized_findings:
            if not _has_raw_markers(normalized_findings):
                regenerated_events: List[Dict[str, Any]] = []
                regenerated = _normalise_findings(normalized_findings, image_id, capture_events=regenerated_events)
                normalized_findings = _validate_findings(dedup_findings(regenerated))
                label_normalization_events = regenerated_events
            else:
                label_normalization_events = _build_label_events_from_findings(normalized_findings)

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
        fallback_meta = coerce_fallback_meta(normalized.get("finding_fallback"))
        fallback_guard = FallbackMetaGuard(fallback_meta, stage="normalized_init")
        if force_dummy_fallback:
            fallback_meta = fallback_meta.mark_forced()
            fallback_guard.update(fallback_meta, stage="forced_param")
        fallback_used = fallback_meta.used
        fallback_strategy = fallback_meta.strategy
        fallback_registry_hit = fallback_meta.registry_hit
        fallback_forced = fallback_meta.forced

        seeded_applied = False
        if (force_dummy_fallback or not normalized_findings) and seeded_records:
            seeded_label_events: List[Dict[str, Any]] = []
            canonical_seeded = _normalise_findings(
                seeded_records,
                image_id,
                capture_events=seeded_label_events,
            )
            normalized_findings = dedup_findings(canonical_seeded or seeded_records)
            normalized_findings = _validate_findings(normalized_findings)
            if seeded_label_events:
                label_normalization_events = seeded_label_events
            seeded_applied = True
            fallback_used = True
            fallback_registry_hit = True
            if not fallback_strategy:
                fallback_strategy = "mock_seed"

        normalized["findings"] = normalized_findings
        normalized["label_normalization"] = list(label_normalization_events)

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
        fallback_meta = fallback_meta.model_copy(update=public_fallback)
        fallback_meta = fallback_meta.with_seeded_ids(list(seeded_finding_ids))
        fallback_guard.update(fallback_meta, stage="public_payload")
        fallback_meta_dict = fallback_guard.snapshot("normalized_payload")
        fallback_meta_dict["seeded_ids_head"] = seeded_finding_ids[:3]
        normalized["finding_fallback"] = fallback_meta_dict
        normalized["finding_source"] = finding_source
        provenance_payload = {
            "finding_source": finding_source,
            "seeded_finding_ids": list(seeded_finding_ids),
            "finding_fallback": fallback_guard.snapshot("provenance_payload"),
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

        debug_builder.record_identity(
            normalized_image=normalized_image,
            image_id=image_id,
            image_id_source=image_id_source,
            storage_uri=storage_uri,
            lookup_hit=bool(lookup_result),
            lookup_source=lookup_source,
            warn_on_lookup_miss=(not lookup_result and image_id_source != "payload"),
            fallback_meta=fallback_guard.snapshot("debug_identity"),
            finding_source=finding_source,
            seeded_finding_ids=seeded_finding_ids,
            provenance=provenance_payload,
            pre_upsert_findings=normalized_findings,
            report_confidence=normalized_report.get("conf"),
            label_normalization=label_normalization_events,
        )

        graph_repo = GraphRepo.from_env()
        finding_verifier = FindingVerifier(graph_repo)
        context_builder = GraphContextBuilder(graph_repo)
        context_orchestrator = ContextOrchestrator(context_builder)

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
        try:
            prepared_graph_payload = graph_repo.prepare_upsert_parameters(graph_payload)
        except ValueError as exc:
            errors.append({"stage": "upsert", "msg": str(exc)})
            raise HTTPException(status_code=422, detail={"ok": False, "errors": errors}) from exc
        if debug_enabled:
            debug_builder.record_upsert_payload(raw_payload=graph_payload, prepared_payload=prepared_graph_payload)
        with timeit(timings, "upsert_ms"):
            upsert_receipt_raw = graph_repo.upsert_case(graph_payload)
        upsert_receipt = dict(upsert_receipt_raw or {})
        resolved_image_id = upsert_receipt.get("image_id")
        if resolved_image_id:
            image_id = resolved_image_id
            normalized_image["image_id"] = resolved_image_id
            normalized["image"]["image_id"] = resolved_image_id
        finding_ids = [fid for fid in list(upsert_receipt.get("finding_ids") or []) if isinstance(fid, str)]
        expected_finding_ids = [str(finding.get("id")) for finding in normalized_findings if isinstance(finding.get("id"), str)]
        verified_finding_ids: List[str] = []

        if expected_finding_ids:
            try:
                if finding_verifier is None:
                    raise RuntimeError("finding verifier unavailable")
                verification = finding_verifier.verify(image_id, expected_finding_ids)
                verified_finding_ids = list(verification.actual)
            except Exception as exc:
                errors.append({"stage": "upsert_verify", "msg": str(exc)})
                raise HTTPException(status_code=500, detail={"ok": False, "errors": errors}) from exc

            receipt_set = set(finding_ids)
            verified_set = set(verified_finding_ids)
            if not receipt_set or not verification.matches or receipt_set != verified_set:
                _raise_upsert_mismatch(
                    expected_ids=expected_finding_ids,
                    receipt_ids=finding_ids,
                    verified_ids=verified_finding_ids,
                    errors_acc=errors,
                    case_id=case_id,
                    image_id=image_id,
                    raw_payload=graph_payload,
                    prepared_payload=prepared_graph_payload,
                )
            logger.info(
                "pipeline.upsert.metrics case=%s image=%s expected_cnt=%s receipt_cnt=%s verified_cnt=%s",
                case_id,
                image_id,
                len(expected_finding_ids),
                len(finding_ids),
                len(verified_finding_ids),
            )

        debug_builder.record_upsert(upsert_receipt, finding_ids, verified_ids=verified_finding_ids or finding_ids)

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
        limits = ContextLimits(
            k_paths=resolved_k_paths,
            max_chars=GRAPH_TRIPLE_CHAR_CAP,
            alpha_finding=alpha_param,
            beta_report=beta_param,
            slot_overrides=slot_overrides or None,
        )
        with timeit(timings, "context_ms"):
            context_result = context_orchestrator.build(
                image_id=image_id,
                normalized_findings=normalized_findings,
                graph_degraded=graph_degraded,
                limits=limits,
            )

        context_bundle = context_result.bundle
        facts = context_result.facts
        findings_list = context_result.findings
        paths_list = context_result.paths
        ctx_paths_total = context_result.path_triple_total
        graph_paths_strength = context_result.graph_paths_strength
        context_no_graph_evidence = context_result.no_graph_evidence
        context_fallback_reason = context_result.fallback_reason
        context_fallback_used = context_result.fallback_used
        no_graph_evidence = context_no_graph_evidence and len(finding_ids) == 0
        has_paths = len(paths_list) > 0
        triples_text = context_bundle.get("triples") if isinstance(context_bundle, dict) else None
        context_mismatch, mismatch_reason = _detect_context_mismatch(paths_list, triples_text)
        if context_mismatch:
            errors.append({"stage": "context", "msg": "facts_paths_mismatch"})

        slot_rebalanced_flag = context_result.slot_rebalanced
        context_notes: List[str] = []
        if isinstance(context_bundle, dict):
            slot_limits_ref = context_bundle.setdefault("slot_limits", {"findings": 0, "reports": 0, "similarity": 0})
            slot_meta_ref = context_bundle.setdefault("slot_meta", {})
            context_bundle["fallback_reason"] = context_fallback_reason
            context_bundle["fallback_used"] = context_fallback_used
            context_bundle["no_graph_evidence"] = context_no_graph_evidence
            context_bundle.setdefault("finding_source", finding_source)
            context_bundle.setdefault("seeded_finding_ids", list(seeded_finding_ids))
            context_bundle.setdefault("finding_fallback", fallback_guard.snapshot("context_bundle"))
            context_bundle.setdefault("finding_provenance", dict(provenance_payload))
            if slot_meta_ref.get("finding_slot_initial") is None:
                slot_meta_ref["finding_slot_initial"] = slot_limits_ref.get("findings", 0)
            slot_meta_ref.setdefault("finding_slot_final", slot_limits_ref.get("findings", 0))
            if slot_meta_ref.get("retried_findings"):
                slot_rebalanced_flag = True
            if slot_rebalanced_flag:
                initial_slot = slot_meta_ref.get("finding_slot_initial")
                final_slot = slot_limits_ref.get("findings")
                if initial_slot is not None and final_slot is not None:
                    note = f"findings slot rebalanced from {initial_slot} to {final_slot}"
                else:
                    note = "findings slot rebalanced to maintain coverage"
                slot_meta_ref.setdefault("notes", []).append(note)
                context_bundle.setdefault("notes", []).append(note)
                context_notes.append(note)

        debug_builder.record_context(
            context_bundle=context_bundle if isinstance(context_bundle, dict) else {},
            findings=findings_list,
            paths=paths_list,
            total_triples=ctx_paths_total,
            graph_paths_strength=graph_paths_strength,
            similar_seed_images=similar_seed_images,
            similarity_edges_created=similarity_edges_created,
            similarity_threshold=similarity_threshold,
            similarity_candidates_considered=similarity_candidates_debug,
            graph_degraded=graph_degraded,
            context_consistency=not context_mismatch,
            context_consistency_reason=mismatch_reason,
            fallback_used=context_fallback_used,
            fallback_reason=context_fallback_reason,
            no_graph_evidence=context_no_graph_evidence,
            notes=context_notes,
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

        weights = {"V": 1.0, "VL": 1.2, "VGL": 1.0}
        if has_paths:
            weights["VGL"] = 1.8 + (0.2 if slot_rebalanced_flag else 0.0)
        elif slot_rebalanced_flag:
            weights["VGL"] += 0.1

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
        debug_builder.record_consensus(consensus)
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
            debug_builder.record_consensus(consensus)
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
        public_fallback_snapshot = fallback_guard.snapshot("results_payload")
        results["finding_fallback"] = public_fallback_snapshot
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
        evaluation_notes_parts: List[str] = []
        if graph_degraded and overall_notes:
            evaluation_notes_parts.append(overall_notes)
        elif evaluation_consensus.get("notes"):
            evaluation_notes_parts.append(evaluation_consensus.get("notes"))
        if context_notes:
            evaluation_notes_parts.extend(context_notes)
        if evaluation_notes_parts:
            evaluation_payload["notes"] = " | ".join(part for part in evaluation_notes_parts if part)
        evaluation_payload["finding_source"] = finding_source
        evaluation_payload["seeded_finding_ids"] = seeded_finding_ids
        evaluation_payload["finding_fallback"] = fallback_guard.snapshot("evaluation_payload")
        evaluation_payload["finding_provenance"] = dict(provenance_payload)
        evaluation_payload["context_fallback_reason"] = context_fallback_reason
        evaluation_payload["context_fallback_used"] = context_fallback_used
        evaluation_payload["context_no_graph_evidence"] = context_no_graph_evidence

        debug_builder.record_evaluation(evaluation_payload)
        if debug_enabled:
            debug_builder.record_fallback_history(fallback_guard.history)

        response = {
            "ok": True,
            "case_id": case_id,
            "image_id": image_id,
            "graph_context": context_bundle,
            "results": results,
            "timings": timings,
            "errors": errors,
            "debug": debug_builder.payload(),
            "evaluation": evaluation_payload,
            "label_normalization": label_normalization_events,
        }
        fallback_guard.ensure(results["finding_fallback"], stage="response.results")
        fallback_guard.ensure(evaluation_payload["finding_fallback"], stage="response.evaluation")
        if overall_status:
            response["status"] = overall_status
        response_notes_parts: List[str] = []
        if overall_notes:
            response_notes_parts.append(overall_notes)
        if context_notes:
            response_notes_parts.extend(context_notes)
        if response_notes_parts:
            response["notes"] = " | ".join(part for part in response_notes_parts if part)

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
