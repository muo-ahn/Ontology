"""
Service wrapper responsible for building graph context bundles plus fallback logic.

Extracted from the /pipeline/analyze router to align with the refactor plan specs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.context_pack import GraphContextBuilder


@dataclass(slots=True)
class ContextLimits:
    """Tunable knobs for graph context generation."""

    k_paths: int
    max_chars: int
    alpha_finding: Optional[float] = None
    beta_report: Optional[float] = None
    slot_overrides: Optional[Dict[str, int]] = None


@dataclass(slots=True)
class ContextResult:
    """Structured output containing the enriched bundle and derived stats."""

    bundle: Dict[str, Any]
    facts: Dict[str, Any]
    findings: List[Dict[str, Any]]
    paths: List[Dict[str, Any]]
    path_triple_total: int
    graph_paths_strength: float
    no_graph_evidence: bool
    fallback_used: bool


class ContextOrchestrator:
    """High-level coordinator that encapsulates GraphContextBuilder usage."""

    def __init__(self, builder: GraphContextBuilder) -> None:
        self._builder = builder

    def build(
        self,
        *,
        image_id: str,
        normalized_findings: List[Dict[str, Any]],
        graph_degraded: bool,
        limits: ContextLimits,
    ) -> ContextResult:
        bundle = self._builder.build_bundle(
            image_id=image_id,
            k=limits.k_paths,
            max_chars=limits.max_chars,
            alpha_finding=limits.alpha_finding,
            beta_report=limits.beta_report,
            k_slots=limits.slot_overrides,
        )

        facts = _safe_dict(bundle.get("facts"))
        findings_list = _extract_findings(facts)

        fallback_used = False
        if graph_degraded and not findings_list and normalized_findings:
            findings_list = _fallback_findings_from_normalized(image_id, normalized_findings)
            if findings_list:
                fallback_used = True
                facts = dict(facts or {})
                facts.setdefault("image_id", image_id)
                facts["findings"] = findings_list
                bundle["facts"] = facts

        raw_paths = bundle.get("paths")
        paths_list = raw_paths if isinstance(raw_paths, list) else []
        ctx_paths_total = _count_triples(paths_list)

        if not paths_list and normalized_findings:
            fallback_paths = _fallback_paths_from_findings(image_id, normalized_findings, limits.k_paths or 1)
            if fallback_paths:
                fallback_used = True
                paths_list = fallback_paths
                bundle["paths"] = fallback_paths
                ctx_paths_total = _count_triples(fallback_paths)

        _ensure_findings_slot_allocation(bundle, len(paths_list))

        graph_paths_strength = _graph_paths_strength(len(paths_list), ctx_paths_total)
        no_graph_evidence = len(paths_list) == 0

        return ContextResult(
            bundle=bundle,
            facts=facts,
            findings=findings_list,
            paths=paths_list,
            path_triple_total=ctx_paths_total,
            graph_paths_strength=graph_paths_strength,
            no_graph_evidence=no_graph_evidence,
            fallback_used=fallback_used,
        )


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_findings(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = facts.get("findings")
    if isinstance(findings, list):
        return list(findings)
    return []


def _fallback_findings_from_normalized(
    image_id: str,
    normalized_findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
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
            "image_id": image_id,
        })
    return fallback_findings


def _fallback_paths_from_findings(
    image_id: Optional[str],
    findings: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not findings or limit <= 0:
        return []
    budget = max(int(limit), 1)
    token = (image_id or "UNKNOWN").strip() or "UNKNOWN"
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


def _graph_paths_strength(path_count: int, triple_total: int) -> float:
    if path_count <= 0 or triple_total <= 0:
        return 0.0
    coverage = min(1.0, path_count / 3.0)
    depth = min(1.0, triple_total / 6.0)
    return round(min(1.0, (coverage * 0.4) + (depth * 0.6)), 3)


def _count_triples(paths: List[Dict[str, Any]]) -> int:
    total = 0
    for path in paths:
        triples = path.get("triples") if isinstance(path, dict) else None
        if isinstance(triples, list):
            total += len(triples)
    return total


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


__all__ = [
    "ContextOrchestrator",
    "ContextLimits",
    "ContextResult",
]
