"""Build structured edge-first context packs and formatted prompt context."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, ConfigDict, model_validator

from .graph_repo import GraphRepo


def json_dumps_safe(obj: Any, *, indent: int = 2) -> str:
    """Serialise objects to JSON while keeping UTF-8 characters."""
    return json.dumps(obj, ensure_ascii=False, indent=indent)


def _render_edge_summary_lines(rows: Sequence[Dict[str, Any]]) -> List[str]:
    lines = ["[EDGE SUMMARY]"]
    if not rows:
        lines.append("데이터 없음")
        return lines
    for row in rows:
        rel = row.get("rel") or row.get("reltype") or "UNKNOWN"
        cnt = row.get("cnt", 0)
        avg_conf = row.get("avg_conf")
        conf_str = "?" if avg_conf is None else f"{float(avg_conf):.2f}"
        lines.append(f"{rel}: cnt={cnt}, avg_conf={conf_str}")
    return lines


def _format_edge_summary(rows: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(_render_edge_summary_lines(rows))


def _build_evidence_paths(paths: Sequence[Dict[str, Any]]) -> List["EvidencePath"]:
    evidence_paths: List[EvidencePath] = []
    for path in paths:
        label = path.get("label") or ""
        triples = path.get("triples") or []
        evidence_paths.append(EvidencePath(label=label, triples=list(triples)))
    return evidence_paths


_PATH_SLOT_KEYS: Sequence[str] = ("findings", "reports", "similarity")


def _sanitise_slot_values(explicit: Optional[Dict[str, int]]) -> Dict[str, int]:
    if not explicit:
        return {}
    clean: Dict[str, int] = {}
    for key in _PATH_SLOT_KEYS:
        if key not in explicit:
            continue
        value = explicit[key]
        try:
            value_int = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"k_slots['{key}'] must be an integer") from exc
        clean[key] = max(value_int, 0)
    return clean


def _cap_slots(slots: Dict[str, int], limit: int) -> Dict[str, int]:
    if limit <= 0:
        return {key: 0 for key in _PATH_SLOT_KEYS}
    order = ("similarity", "reports", "findings")
    capped = {key: max(int(slots.get(key, 0)), 0) for key in _PATH_SLOT_KEYS}
    while sum(capped.values()) > limit:
        for key in order:
            if capped[key] > 0:
                capped[key] -= 1
                if sum(capped.values()) <= limit:
                    break
        else:  # pragma: no cover - defensive; should not occur
            break
    return capped


def _resolve_path_slots(total: int, explicit: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    total_budget = max(int(total), 0)
    explicit_clean = _sanitise_slot_values(explicit)
    if explicit_clean:
        return _cap_slots(explicit_clean, total_budget)

    slots = {key: 0 for key in _PATH_SLOT_KEYS}
    if total_budget == 0:
        return slots

    remaining = total_budget
    slots["findings"] = min(2, remaining)
    remaining -= slots["findings"]

    if remaining > 0:
        slots["reports"] = min(2, remaining)
        remaining -= slots["reports"]

    if remaining > 0:
        slots["similarity"] = remaining

    return slots


def _dedupe_path_rows(paths: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for row in paths:
        label = str(row.get("label") or "")
        triples_raw = row.get("triples") or []
        triples = tuple(str(item) for item in triples_raw if item is not None)
        if not triples:
            continue
        key = (label, triples)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({
            "label": label,
            "triples": list(triples),
            "score": row.get("score"),
        })
    return deduped


class GraphContextBuilder:
    """Fetches and formats graph-derived context for LLM prompts."""

    def __init__(self, repo: Optional[GraphRepo] = None) -> None:
        self._repo = repo or GraphRepo.from_env()
        self._owns_repo = repo is None

    def close(self) -> None:
        if self._owns_repo:
            self._repo.close()

    def build_prompt_context(
        self,
        image_id: str,
        k: int = 2,
        mode: str = "triples",
        *,
        max_chars: int = 1800,
        alpha_finding: Optional[float] = None,
        beta_report: Optional[float] = None,
        k_slots: Optional[Dict[str, int]] = None,
    ) -> str:
        mode_normalised = mode.lower()
        if mode_normalised not in {"triples", "json"}:
            raise ValueError("mode must be 'triples' or 'json'")

        if mode_normalised == "json":
            bundle = self._repo.query_bundle(image_id)
            facts = ContextFacts(**bundle.get("facts", {"image_id": image_id, "findings": []}))
            return json_dumps_safe(facts.model_dump(mode="python"))

        bundle = self.build_bundle(
            image_id=image_id,
            k=k,
            max_chars=max_chars,
            alpha_finding=alpha_finding,
            beta_report=beta_report,
            k_slots=k_slots,
        )
        return bundle["triples"]

    def build_bundle(
        self,
        image_id: str,
        *,
        k: int = 2,
        max_chars: int = 1800,
        hard_trim: bool = True,
        alpha_finding: Optional[float] = None,
        beta_report: Optional[float] = None,
        k_slots: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        if k < 0:
            raise ValueError("k must be >= 0")

        bundle_payload = self._repo.query_bundle(image_id)
        summary_rows = bundle_payload.get("summary", [])
        facts_data = bundle_payload.get("facts", {"image_id": image_id, "findings": []})
        facts = ContextFacts(**facts_data)
        facts_payload = facts.model_dump(mode="python")

        summary_text = _format_edge_summary(summary_rows)
        current_k = max(k, 0)
        slot_overrides = dict(k_slots or {})

        def _render(current_paths: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
            evidence_paths = _build_evidence_paths(current_paths)
            evidence_section = self._format_evidence_section(evidence_paths)
            sections = [
                summary_text,
                evidence_section,
                "[FACTS JSON]",
                json_dumps_safe(facts_payload),
            ]
            triples_text = "\n".join(section for section in sections if section)
            return {
                "summary_text": summary_text,
                "evidence_paths": evidence_paths,
                "triples_text": triples_text,
            }

        paths_rows: Sequence[Dict[str, Any]] = []
        rendered_bundle: Dict[str, Any] = {}
        final_slot_limits: Dict[str, int] = {}
        while True:
            slot_limits = _resolve_path_slots(current_k, slot_overrides)
            raw_paths = self._repo.query_paths(
                image_id,
                current_k,
                alpha_finding=alpha_finding,
                beta_report=beta_report,
                k_slots=slot_limits,
            )
            paths_rows = _dedupe_path_rows(raw_paths)
            rendered = _render(paths_rows)
            triples_text = rendered["triples_text"]
            if max_chars and max_chars > 0 and len(triples_text) > max_chars and current_k > 0:
                if current_k == 0:
                    break
                current_k -= 1
                continue
            rendered_bundle = rendered
            final_slot_limits = slot_limits
            break

        triples_text = rendered_bundle.get("triples_text", "")
        if max_chars and max_chars > 0 and len(triples_text) > max_chars and hard_trim:
            trimmed = triples_text[: max_chars - 1].rstrip()
            triples_text = f"{trimmed}…"

        summary_lines = [line for line in _render_edge_summary_lines(summary_rows) if line]
        paths_payload = [
            {"label": path.label, "triples": path.triples}
            for path in rendered_bundle.get("evidence_paths", [])
        ]

        return {
            "summary": summary_lines,
            "paths": paths_payload,
            "facts": facts_payload,
            "triples": triples_text,
            "slot_limits": final_slot_limits,
        }

    def _format_evidence_section(self, paths: Sequence["EvidencePath"]) -> str:
        lines = ["[EVIDENCE PATHS (Top-k)]"]
        if not paths:
            lines.append("데이터 없음")
            return "\n".join(lines)
        for idx, path in enumerate(paths, start=1):
            lines.append(f"{idx}) {path.label}")
            for triple in path.triples:
                lines.append(f"   {triple}")
        return "\n".join(lines)


class EvidencePath(BaseModel):
    """Readable description of a reasoning path through the graph."""

    label: str
    triples: List[str] = Field(default_factory=list)


class ContextFacts(BaseModel):
    """Normalised JSON facts injected alongside the evidence summary."""

    model_config = ConfigDict(populate_by_name=True)

    image_id: str
    findings: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _promote_legacy_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "image_id" not in data and data.get("id"):
            data = dict(data)
            data["image_id"] = data["id"]
        return data


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

    def build(self, image_id: str, *, k: Optional[int] = None, k_slots: Optional[Dict[str, int]] = None) -> ContextPack:
        k_raw = self.top_k_paths if k is None else k
        if k_raw < 0:
            raise ValueError("k must be >= 0")
        k_value = k_raw
        bundle_payload = self._repo.query_bundle(image_id)
        summary_rows = bundle_payload.get("summary", [])
        facts_payload = bundle_payload.get("facts", {"image_id": image_id, "findings": []})
        facts = ContextFacts(**facts_payload)

        edge_summary = _format_edge_summary(summary_rows)
        slot_limits = _resolve_path_slots(k_value, k_slots)
        path_rows = self._repo.query_paths(image_id, k_value, k_slots=slot_limits)
        evidence_paths = _build_evidence_paths(_dedupe_path_rows(path_rows))

        return ContextPack(edge_summary=edge_summary, evidence_paths=evidence_paths, facts=facts)


__all__ = [
    "json_dumps_safe",
    "GraphContextBuilder",
    "ContextPack",
    "ContextPackBuilder",
    "ContextFacts",
    "EvidencePath",
]
