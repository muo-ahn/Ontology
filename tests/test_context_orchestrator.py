from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.context_orchestrator import ContextLimits, ContextOrchestrator


class _FakeBuilder:
    def __init__(self, bundle: Dict[str, Any]) -> None:
        self._bundle = bundle

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
        payload = dict(self._bundle)
        payload.setdefault("slot_limits", {"findings": 1, "reports": 0, "similarity": 0})
        payload.setdefault("slot_meta", {"allocated_total": sum(payload["slot_limits"].values()), "slot_source": "auto"})
        payload.setdefault("triples", "")
        payload.setdefault("summary", ["[EDGE SUMMARY]", "데이터 없음"])
        payload.setdefault("facts", {"image_id": image_id, "findings": payload.get("facts", {}).get("findings", [])})
        return payload


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


def test_context_orchestrator_fills_missing_facts_when_graph_degraded() -> None:
    bundle = _context(findings=[], paths=[], graph_degraded=True)
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    normalized = [{"id": "F2", "type": "nodule", "location": "lung", "conf": 0.7}]
    result = orchestrator.build(
        image_id="IMG123",
        normalized_findings=normalized,
        graph_degraded=True,
        limits=_limits(),
    )
    assert result.fallback_used
    assert result.findings[0]["id"] == "F2"
    assert result.paths, "fallback paths should be synthesised"
    assert result.bundle["facts"]["findings"][0]["image_id"] == "IMG123"


def test_context_orchestrator_generates_paths_when_none_exist() -> None:
    bundle = _context(findings=[{"id": "F1", "type": "mass"}], paths=[])
    orchestrator = ContextOrchestrator(_FakeBuilder(bundle))
    result = orchestrator.build(
        image_id="IMG777",
        normalized_findings=[{"id": "F1", "type": "mass"}],
        graph_degraded=False,
        limits=_limits(k_paths=1),
    )
    assert result.paths, "should fallback to synthesized paths"
    assert result.paths[0]["slot"] == "findings"
    assert result.graph_paths_strength > 0
    assert not result.no_graph_evidence


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
