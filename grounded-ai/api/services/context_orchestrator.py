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
    fallback_reason: Optional[str]
    slot_rebalanced: bool


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
        context_data = self._builder.build_context(
            image_id=image_id,
            k=limits.k_paths,
            max_chars=limits.max_chars,
            alpha_finding=limits.alpha_finding,
            beta_report=limits.beta_report,
            k_slots=limits.slot_overrides,
        )
        bundle = context_data.to_bundle()
        facts = _safe_dict(context_data.facts)
        findings_list = _extract_findings(facts)
        paths_list = list(context_data.paths or [])
        ctx_paths_total = _count_triples(paths_list)
        slot_rebalanced = _ensure_findings_slot_allocation(bundle, len(paths_list))

        graph_paths_strength = _graph_paths_strength(len(paths_list), ctx_paths_total)
        no_graph_evidence = len(paths_list) == 0
        fallback_reason: Optional[str] = None
        if no_graph_evidence:
            fallback_reason = "no_graph_paths"
        elif graph_degraded:
            fallback_reason = "graph_degraded"
        fallback_used = fallback_reason is not None

        return ContextResult(
            bundle=bundle,
            facts=facts,
            findings=findings_list,
            paths=paths_list,
            path_triple_total=ctx_paths_total,
            graph_paths_strength=graph_paths_strength,
            no_graph_evidence=no_graph_evidence,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            slot_rebalanced=slot_rebalanced or bool((_safe_dict(bundle.get("slot_meta"))).get("retried_findings")),
        )


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_findings(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = facts.get("findings")
    if isinstance(findings, list):
        return list(findings)
    return []


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


def _ensure_findings_slot_allocation(bundle: Dict[str, Any], minimum: int) -> bool:
    if minimum <= 0:
        return False
    slot_limits = bundle.get("slot_limits")
    initial_value = 0
    if not isinstance(slot_limits, dict):
        slot_limits = {"findings": minimum, "reports": 0, "similarity": 0}
        bundle["slot_limits"] = slot_limits
        changed = True
    else:
        initial_value = int(slot_limits.get("findings", 0))
        new_value = max(initial_value, minimum)
        changed = new_value != initial_value
        slot_limits["findings"] = new_value
    slot_meta = bundle.get("slot_meta")
    if not isinstance(slot_meta, dict):
        slot_meta = {"slot_source": "auto"}
        bundle["slot_meta"] = slot_meta
    if "finding_slot_initial" not in slot_meta:
        slot_meta["finding_slot_initial"] = initial_value
    slot_meta["finding_slot_final"] = slot_limits.get("findings", minimum)
    try:
        allocated_total = sum(max(int(slot_limits.get(key, 0)), 0) for key in ("findings", "reports", "similarity"))
    except Exception:
        allocated_total = minimum
    slot_meta["allocated_total"] = max(int(slot_meta.get("allocated_total", 0)), allocated_total)
    if changed:
        slot_meta["retried_findings"] = True
    return changed


__all__ = [
    "ContextOrchestrator",
    "ContextLimits",
    "ContextResult",
]
