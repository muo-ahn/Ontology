"""Build structured edge-first context packs and formatted prompt context."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pydantic import BaseModel, Field

from .graph_repo import GraphRepo


def json_dumps_safe(obj: Any, *, indent: int = 2) -> str:
    """Serialise objects to JSON while keeping UTF-8 characters."""
    return json.dumps(obj, ensure_ascii=False, indent=indent)


def _format_edge_summary(rows: Sequence[Dict[str, Any]]) -> str:
    lines = ["[EDGE SUMMARY]"]
    if not rows:
        lines.append("데이터 없음")
        return "\n".join(lines)
    for row in rows:
        rel = row.get("reltype") or "UNKNOWN"
        cnt = row.get("cnt", 0)
        avg_conf = row.get("avg_conf")
        conf_str = "?" if avg_conf is None else f"{avg_conf:.2f}"
        lines.append(f"{rel}: cnt={cnt}, avg_conf={conf_str}")
    return "\n".join(lines)


def _combine_label(label: str, parts: List[str]) -> str:
    if not parts:
        return label
    return f"{label} | {', '.join(parts)}"


def _format_finding_label(finding: Dict[str, Any]) -> str:
    label = finding.get("type") or finding.get("id") or "Finding"
    if finding.get("id"):
        label = f"{label}#{finding['id']}"
    parts: List[str] = []
    if finding.get("location"):
        parts.append(f"location={finding['location']}")
    if finding.get("size_cm") is not None:
        parts.append(f"size_cm={finding['size_cm']}")
    if finding.get("conf") is not None:
        parts.append(f"conf={finding['conf']:.2f}")
    return _combine_label(label, parts)


def _format_anatomy_label(anatomy: Dict[str, Any]) -> str:
    if not anatomy:
        return ""
    label = anatomy.get("name") or anatomy.get("id") or "Anatomy"
    if anatomy.get("id") and anatomy.get("name"):
        label = f"{label}#{anatomy['id']}"
    return label


def _format_report_label(report: Dict[str, Any]) -> str:
    if not report:
        return ""
    label = f"Report#{report.get('id')}" if report.get("id") else "Report"
    parts: List[str] = []
    if report.get("model"):
        parts.append(f"model={report['model']}")
    if report.get("conf") is not None:
        parts.append(f"conf={report['conf']:.2f}")
    if report.get("ts"):
        parts.append(f"ts={report['ts']}")
    return _combine_label(label, parts)


def _render_path_lines(index: int, id: str, hit: Dict[str, Any]) -> List[str]:
    finding = hit.get("f") or {}
    anatomy = hit.get("a") or {}
    report = hit.get("rep") or {}
    score = hit.get("score")

    finding_label = _format_finding_label(finding)
    suffix = f" [score={score:.2f}]" if isinstance(score, (int, float)) else ""

    lines = [
        f"{index}) (Image {id})-[HAS_FINDING]->({finding_label}){suffix}",
    ]

    anatomy_label = _format_anatomy_label(anatomy)
    if anatomy_label:
        lines.append(f"   ({finding_label})-[LOCATED_IN]->({anatomy_label})")

    report_label = _format_report_label(report)
    if report_label:
        lines.append(f"   (Image {id})-[DESCRIBED_BY]->({report_label})")

    return lines


def _build_evidence_paths(id: str, hits: Sequence[Dict[str, Any]]) -> List["EvidencePath"]:
    evidence_paths: List[EvidencePath] = []
    for hit in hits:
        finding = hit.get("f") or {}
        anatomy = hit.get("a") or {}
        report = hit.get("rep") or {}
        score = hit.get("score")

        finding_label = _format_finding_label(finding)
        triples: List[str] = [
            f"(Image {id}) -[HAS_FINDING]-> ({finding_label})"
        ]

        anatomy_label = _format_anatomy_label(anatomy)
        if anatomy_label:
            triples.append(f"({finding_label}) -[LOCATED_IN]-> ({anatomy_label})")

        report_label = _format_report_label(report)
        if report_label:
            triples.append(f"(Image {id}) -[DESCRIBED_BY]-> ({report_label})")

        label = finding.get("type") or finding.get("id") or "Finding"
        if isinstance(score, (int, float)):
            label = f"{label} [score={score:.2f}]"
        evidence_paths.append(EvidencePath(label=label, triples=triples))
    return evidence_paths


class GraphContextBuilder:
    """Fetches and formats graph-derived context for LLM prompts."""

    def __init__(self, repo: Optional[GraphRepo] = None) -> None:
        self._repo = repo or GraphRepo.from_env()
        self._owns_repo = repo is None

    def close(self) -> None:
        if self._owns_repo:
            self._repo.close()

    def build_prompt_context(self, id: str, k: int = 2, mode: str = "triples") -> str:
        mode_normalised = mode.lower()
        if mode_normalised not in {"triples", "json"}:
            raise ValueError("mode must be 'triples' or 'json'")

        edge_rows = self._repo.query_edge_summary(id)
        topk_records = self._repo.query_topk_paths(id, k)
        hits = list(topk_records[0].get("hits", [])) if topk_records else []
        facts = self._repo.query_facts(id)

        if mode_normalised == "json":
            return json_dumps_safe(facts)

        sections = [
            _format_edge_summary(edge_rows),
            self._format_evidence_section(id, hits),
            "[FACTS JSON]",
            json_dumps_safe(facts),
        ]
        return "\n".join(section for section in sections if section)

    def _format_evidence_section(self, id: str, hits: Sequence[Dict[str, Any]]) -> str:
        lines = ["[EVIDENCE PATHS (Top-k)]"]
        if not hits:
            lines.append("데이터 없음")
            return "\n".join(lines)
        for idx, hit in enumerate(hits, start=1):
            lines.extend(_render_path_lines(idx, id, hit))
        return "\n".join(lines)


class EvidencePath(BaseModel):
    """Readable description of a reasoning path through the graph."""

    label: str
    triples: List[str] = Field(default_factory=list)


class ContextFacts(BaseModel):
    """Normalised JSON facts injected alongside the evidence summary."""

    id: str
    findings: List[Dict[str, Any]] = Field(default_factory=list)


class ContextPack(BaseModel):
    """Edge-first bundle combining summaries, paths and raw facts."""

    edge_summary: str
    evidence_paths: List[EvidencePath] = Field(default_factory=list)
    facts: ContextFacts


class ContextPackBuilder:
    """Derives a compact yet faithful context description from graph payloads."""

    def __init__(self, repo: Optional[GraphRepo] = None, top_k_paths: int = 3) -> None:
        self._repo = repo or GraphRepo.from_env()
        self._owns_repo = repo is None
        self.top_k_paths = top_k_paths

    def close(self) -> None:
        if self._owns_repo:
            self._repo.close()

    def build(self, id: str, *, k: Optional[int] = None) -> ContextPack:
        k_value = k or self.top_k_paths
        edge_rows = self._repo.query_edge_summary(id)
        topk_records = self._repo.query_topk_paths(id, k_value)
        hits = list(topk_records[0].get("hits", [])) if topk_records else []
        facts_raw = self._repo.query_facts(id)
        facts = ContextFacts(**facts_raw)

        edge_summary = _format_edge_summary(edge_rows)
        evidence_paths = _build_evidence_paths(id, hits)

        return ContextPack(edge_summary=edge_summary, evidence_paths=evidence_paths, facts=facts)


__all__ = [
    "json_dumps_safe",
    "GraphContextBuilder",
    "ContextPack",
    "ContextPackBuilder",
    "ContextFacts",
    "EvidencePath",
]

