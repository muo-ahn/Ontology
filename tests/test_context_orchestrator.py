from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.context_orchestrator import ContextLimits, ContextOrchestrator
from services.context_pack import GraphContextResult


class _FakeBuilder:
    def __init__(self, bundle: Dict[str, Any]) -> None:
        self._bundle = bundle

    def _prepare_bundle(self, image_id: str) -> Dict[str, Any]:
        payload = dict(self._bundle)
        payload.setdefault("summary", ["[EDGE SUMMARY]", "데이터 없음"])
        payload.setdefault("summary_rows", [])
        facts = dict(payload.get("facts") or {"image_id": image_id, "findings": []})
        facts.setdefault("image_id", image_id)
        facts.setdefault("findings", [])
        payload["facts"] = facts
        payload.setdefault("paths", [])
        payload.setdefault("triples", "\n".join(payload["summary"] + ["[EVIDENCE PATHS (Top-k)]", "No path generated"]))
        payload.setdefault("slot_limits", {"findings": 1, "reports": 0, "similarity": 0})
        slot_limits = payload["slot_limits"]
        payload.setdefault(
            "slot_meta",
            {"allocated_total": sum(slot_limits.values()), "slot_source": "auto", "requested_k": 1, "applied_k": 1, "requested_overrides": {}},
        )
        return payload

    def build_context(
        self,
        *,
        image_id: str,
        k: int,
        max_chars: int,
        alpha_finding: float | None,
        beta_report: float | None,
        k_slots: Dict[str, int] | None,
    ) -> GraphContextResult:
        payload = self._prepare_bundle(image_id)
        return GraphContextResult(
            summary=list(payload["summary"]),
            summary_rows=list(payload.get("summary_rows", [])),
            paths=list(payload.get("paths", [])),
            facts=dict(payload["facts"]),
            triples_text=str(payload.get("triples", "")),
            slot_limits=dict(payload.get("slot_limits", {})),
            slot_meta=dict(payload.get("slot_meta", {})),
        )

    def build_bundle(
        self,
        *,
        image_id: str,
        k: int,
        max_chars: int,
        alpha_finding: float | None,
        beta_report: float | None,
        k_slots: Dict[str, int] | None,
    ) -> Dict[str, Any]:
        return self.build_context(
            image_id=image_id,
            k=k,
            max_chars=max_chars,
            alpha_finding=alpha_finding,
            beta_report=beta_report,
            k_slots=k_slots,
        ).to_bundle()


def _context(
    *,
    findings: List[Dict[str, Any]] | None = None,
    paths: List[Dict[str, Any]] | None = None,
    graph_degraded: bool = False,
) -> Dict[str, Any]:
    facts = {"image_id": "IMG001"}
    if findings is not None:
        facts["findings"] = findings
    bundle: Dict[str, Any] = {
        "facts": facts,
    }
    if paths is not None:
        bundle["paths"] = paths
    if graph_degraded:
        bundle.setdefault("slot_limits", {"findings": 1})
    return bundle


def _limits(k_paths: int = 2) -> ContextLimits:
    return ContextLimits(k_paths=k_paths, max_chars=1800)


def test_context_orchestrator_keeps_existing_paths_and_facts() -> None:
    bundle = _context(
        findings=[{"id": "F1", "type": "mass", "location": "liver", "conf": 0.9}],
        paths=[{"slot": "findings", "triples": ["Image -HAS_FINDING-> Finding[F1]"], "score": 0.9}],
    )
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    result = orchestrator.build(
        image_id="IMG001",
        normalized_findings=[{"id": "F1"}],
        graph_degraded=False,
        limits=_limits(),
    )
    assert result.bundle["facts"]["findings"][0]["id"] == "F1"
    assert result.paths[0]["triples"][0].startswith("Image")
    assert result.graph_paths_strength > 0
    assert not result.no_graph_evidence


def test_context_orchestrator_reports_no_graph_evidence_for_empty_bundle() -> None:
    bundle = _context(findings=[], paths=[], graph_degraded=True)
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    normalized = [{"id": "F2", "type": "nodule", "location": "lung", "conf": 0.7}]
    result = orchestrator.build(
        image_id="IMG123",
        normalized_findings=normalized,
        graph_degraded=True,
        limits=_limits(),
    )
    assert result.no_graph_evidence
    assert result.fallback_used
    assert result.fallback_reason == "no_graph_paths"
    assert result.findings == []
    assert result.paths == []


def test_context_orchestrator_does_not_synthesise_paths() -> None:
    bundle = _context(findings=[{"id": "F1", "type": "mass"}], paths=[])
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    result = orchestrator.build(
        image_id="IMG777",
        normalized_findings=[{"id": "F1", "type": "mass"}],
        graph_degraded=False,
        limits=_limits(k_paths=1),
    )
    assert not result.paths
    assert result.no_graph_evidence
    assert result.fallback_used
    assert result.fallback_reason == "no_graph_paths"


def test_context_orchestrator_marks_no_graph_evidence_when_everything_empty() -> None:
    bundle = _context(findings=[], paths=[])
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    result = orchestrator.build(
        image_id="IMG000",
        normalized_findings=[],
        graph_degraded=False,
        limits=_limits(),
    )
    assert result.no_graph_evidence
    assert result.graph_paths_strength == 0
