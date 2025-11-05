"""Build structured edge-first context packs and formatted prompt context."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, ConfigDict, model_validator

from .graph_repo import GraphRepo


_PATH_SLOT_KEYS: Sequence[str] = ("findings", "reports", "similarity")
_RELATION_KEYS = {
    "HAS_FINDING",
    "LOCATED_IN",
    "RELATED_TO",
    "DESCRIBED_BY",
    "HAS_IMAGE",
    "HAS_ENCOUNTER",
    "HAS_INFERENCE",
    "SIMILAR_TO",
}
_RELATION_PATTERN = re.compile(r"-\s*([A-Z_]+)\s*->")
_FINDING_ID_PATTERN = re.compile(r"Finding\[(?P<id>[^\]]+)\]")
_SUMMARY_REL_ORDER: Sequence[str] = (
    "HAS_FINDING",
    "LOCATED_IN",
    "RELATED_TO",
    "DESCRIBED_BY",
    "HAS_IMAGE",
    "HAS_ENCOUNTER",
    "HAS_INFERENCE",
    "SIMILAR_TO",
)


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


def _categorise_path_slot(row: Dict[str, Any]) -> str:
    slot = str(row.get("slot") or row.get("category") or "").strip().lower()
    if slot in _PATH_SLOT_KEYS:
        return slot
    triples = row.get("triples") or []
    for triple in triples:
        fragment = str(triple)
        if "SIMILAR_TO" in fragment:
            return "similarity"
        if "DESCRIBED_BY" in fragment or "MENTIONS" in fragment:
            return "reports"
        if "HAS_FINDING" in fragment:
            return "findings"
    return ""


def _build_evidence_paths(paths: Sequence[Dict[str, Any]]) -> List["EvidencePath"]:
    evidence_paths: List[EvidencePath] = []
    for path in paths:
        label = path.get("label") or ""
        triples = path.get("triples") or []
        slot = _categorise_path_slot(path) or None
        evidence_paths.append(EvidencePath(label=label, triples=list(triples), slot=slot))
    return evidence_paths


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
        slot = _categorise_path_slot(row) or None
        deduped.append({
            "label": label,
            "triples": list(triples),
            "score": row.get("score"),
            "slot": slot,
        })
    return deduped


def _rebalance_slot_limits(slots: Dict[str, int], paths: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    total = sum(max(int(slots.get(key, 0)), 0) for key in _PATH_SLOT_KEYS)
    if total <= 0:
        return slots

    counts: Dict[str, int] = {key: 0 for key in _PATH_SLOT_KEYS}
    for row in paths:
        slot = _categorise_path_slot(row)
        if slot in counts:
            counts[slot] += 1

    order = list(_PATH_SLOT_KEYS)
    if counts.get("findings", 0) == 0:
        order = ["reports", "similarity", "findings"]

    current = {key: max(int(slots.get(key, 0)), 0) for key in _PATH_SLOT_KEYS}
    rebalanced = {key: 0 for key in _PATH_SLOT_KEYS}
    remaining = total

    primary = [key for key in order if counts.get(key, 0) > 0]
    secondary = [key for key in order if key not in primary]

    for key in primary:
        if remaining <= 0:
            break
        desired = current.get(key, 0) or 1
        allocation = min(remaining, max(desired, 1))
        rebalanced[key] = allocation
        remaining -= allocation

    for key in secondary:
        if remaining <= 0:
            break
        if rebalanced[key] == 0:
            rebalanced[key] = 1
            remaining -= 1

    distribution_order = [key for key in order if rebalanced.get(key, 0) > 0]
    if not distribution_order:
        distribution_order = order

    idx = 0
    while remaining > 0 and distribution_order:
        key = distribution_order[idx % len(distribution_order)]
        rebalanced[key] += 1
        remaining -= 1
        idx += 1

    return rebalanced


def _extract_relation(token: str) -> Optional[str]:
    match = _RELATION_PATTERN.search(token)
    if not match:
        return None
    relation = match.group(1)
    return relation if relation in _RELATION_KEYS else None


def _augment_summary_rows(
    summary_rows: Sequence[Dict[str, Any]],
    paths: Sequence[Dict[str, Any]],
    facts_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    summary_map: Dict[str, Dict[str, Any]] = {}
    for row in summary_rows or []:
        relation = row.get("rel") or row.get("reltype")
        if not relation:
            continue
        summary_map[relation] = dict(row)

    finding_conf_map: Dict[str, Optional[float]] = {}
    if isinstance(facts_payload, dict):
        for finding in facts_payload.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            fid = str(finding.get("id") or finding.get("finding_id") or "").strip()
            if not fid:
                continue
            conf_raw = finding.get("conf")
            try:
                finding_conf_map[fid] = float(conf_raw) if conf_raw is not None else None
            except (TypeError, ValueError):
                finding_conf_map[fid] = None

    fallback_counts: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        triples = path.get("triples") or []
        for triple in triples:
            relation = _extract_relation(str(triple))
            if not relation:
                continue
            entry = fallback_counts.setdefault(relation, {"cnt": 0, "conf": []})
            entry["cnt"] += 1
            confidence_value: Optional[float] = None
            if relation == "HAS_FINDING":
                match = _FINDING_ID_PATTERN.search(str(triple))
                if match:
                    confidence_value = finding_conf_map.get(match.group("id"))
            elif relation == "SIMILAR_TO":
                score = path.get("score")
                try:
                    confidence_value = float(score) if score is not None else None
                except (TypeError, ValueError):
                    confidence_value = None
            if confidence_value is not None:
                entry["conf"].append(confidence_value)

    for relation, info in fallback_counts.items():
        if relation in summary_map:
            continue
        if info.get("cnt", 0) <= 0:
            continue
        values = info.get("conf") or []
        avg_conf = None
        if values:
            avg_conf = round(sum(values) / len(values), 2)
        summary_map[relation] = {
            "rel": relation,
            "cnt": info["cnt"],
            "avg_conf": avg_conf,
        }

    ordered: List[Dict[str, Any]] = []
    for relation in _SUMMARY_REL_ORDER:
        row = summary_map.get(relation)
        if row:
            ordered.append(row)
    for relation, row in summary_map.items():
        if relation not in _SUMMARY_REL_ORDER:
            ordered.append(row)
    return ordered


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

        requested_k = max(int(k), 0)
        bundle_payload = self._repo.query_bundle(image_id)
        summary_rows = bundle_payload.get("summary", [])
        facts_data = bundle_payload.get("facts", {"image_id": image_id, "findings": []})
        facts = ContextFacts(**facts_data)
        facts_payload = facts.model_dump(mode="python")

        current_k = requested_k
        slot_overrides = dict(k_slots or {})

        def _render(current_paths: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
            effective_summary_rows = _augment_summary_rows(summary_rows, current_paths, facts_payload)
            summary_text_local = _format_edge_summary(effective_summary_rows)
            evidence_paths = _build_evidence_paths(current_paths)
            evidence_section = self._format_evidence_section(evidence_paths)
            sections = [
                summary_text_local,
                evidence_section,
                "[FACTS JSON]",
                json_dumps_safe(facts_payload),
            ]
            triples_text = "\n".join(section for section in sections if section)
            return {
                "summary_text": summary_text_local,
                "summary_rows": effective_summary_rows,
                "evidence_paths": evidence_paths,
                "triples_text": triples_text,
            }

        paths_rows: Sequence[Dict[str, Any]] = []
        rendered_bundle: Dict[str, Any] = {}
        final_slot_limits: Dict[str, int] = {}
        slot_limits = _resolve_path_slots(current_k, slot_overrides)
        attempted_slot_configs: set[tuple[tuple[str, int], ...]] = set()
        deduped_rows: List[Dict[str, Any]] = []
        while True:
            slot_signature = tuple((key, int(slot_limits.get(key, 0))) for key in _PATH_SLOT_KEYS)
            if slot_signature in attempted_slot_configs:
                break
            attempted_slot_configs.add(slot_signature)

            raw_paths = self._repo.query_paths(
                image_id,
                current_k,
                alpha_finding=alpha_finding,
                beta_report=beta_report,
                k_slots=slot_limits,
            )
            paths_rows = _dedupe_path_rows(raw_paths)

            total_budget = sum(max(int(slot_limits.get(key, 0)), 0) for key in _PATH_SLOT_KEYS)
            desired_paths = total_budget if current_k <= 0 else min(current_k, total_budget or current_k)
            if (
                not slot_overrides
                and desired_paths > 0
                and len(paths_rows) < desired_paths
            ):
                rebalanced = _rebalance_slot_limits(slot_limits, paths_rows)
                if rebalanced != slot_limits:
                    slot_limits = rebalanced
                    continue

            rendered = _render(paths_rows)
            triples_text = rendered["triples_text"]
            if max_chars and max_chars > 0 and len(triples_text) > max_chars and current_k > 0:
                current_k -= 1
                slot_limits = _resolve_path_slots(current_k, slot_overrides)
                attempted_slot_configs.clear()
                continue

            rendered_bundle = rendered
            final_slot_limits = slot_limits
            break

        applied_k = current_k
        try:
            slot_overrides_clean = _sanitise_slot_values(slot_overrides)
        except ValueError:
            slot_overrides_clean = {}

        triples_text = rendered_bundle.get("triples_text", "")
        if max_chars and max_chars > 0 and len(triples_text) > max_chars and hard_trim:
            trimmed = triples_text[: max_chars - 1].rstrip()
            triples_text = f"{trimmed}..."

        final_summary_rows = rendered_bundle.get("summary_rows", summary_rows)
        summary_lines = [line for line in _render_edge_summary_lines(final_summary_rows) if line]
        paths_payload = [
            {
                "label": path.label,
                "triples": path.triples,
                "slot": path.slot,
            }
            for path in rendered_bundle.get("evidence_paths", [])
        ]
        slot_meta = {
            "requested_k": requested_k,
            "applied_k": applied_k,
            "slot_source": "overrides" if slot_overrides_clean else "auto",
            "requested_overrides": slot_overrides_clean,
            "allocated_total": sum(max(int(final_slot_limits.get(key, 0)), 0) for key in _PATH_SLOT_KEYS),
        }
        if slot_overrides and slot_overrides_clean != slot_overrides:
            slot_meta["requested_overrides_raw"] = slot_overrides

        return {
            "summary": summary_lines,
            "paths": paths_payload,
            "facts": facts_payload,
            "triples": triples_text,
            "slot_limits": final_slot_limits,
            "slot_meta": slot_meta,
            "summary_rows": final_summary_rows,
        }

    def _format_evidence_section(self, paths: Sequence["EvidencePath"]) -> str:
        lines = ["[EVIDENCE PATHS (Top-k)]"]
        if not paths:
            lines.append("데이터 없음")
            return "\n".join(lines)
        for idx, path in enumerate(paths, start=1):
            slot_prefix = f"[{path.slot}] " if path.slot else ""
            lines.append(f"{idx}) {slot_prefix}{path.label}")
            for triple in path.triples:
                lines.append(f"   {triple}")
        return "\n".join(lines)


class EvidencePath(BaseModel):
    """Readable description of a reasoning path through the graph."""

    label: str
    triples: List[str] = Field(default_factory=list)
    slot: Optional[str] = None


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
    slot_limits: Dict[str, int] = Field(default_factory=dict)
    slot_meta: Dict[str, Any] = Field(default_factory=dict)


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

        slot_limits = _resolve_path_slots(k_value, k_slots)
        attempted_slot_configs: set[tuple[tuple[str, int], ...]] = set()
        final_deduped_rows: List[Dict[str, Any]] = []
        while True:
            slot_signature = tuple((key, int(slot_limits.get(key, 0))) for key in _PATH_SLOT_KEYS)
            if slot_signature in attempted_slot_configs:
                break
            attempted_slot_configs.add(slot_signature)

            path_rows = self._repo.query_paths(image_id, k_value, k_slots=slot_limits)
            deduped_rows = _dedupe_path_rows(path_rows)
            final_deduped_rows = deduped_rows
            total_budget = sum(max(int(slot_limits.get(key, 0)), 0) for key in _PATH_SLOT_KEYS)
            desired_paths = total_budget if k_value <= 0 else min(k_value, total_budget or k_value)
            if (
                not k_slots
                and desired_paths > 0
                and len(deduped_rows) < desired_paths
            ):
                rebalanced = _rebalance_slot_limits(slot_limits, deduped_rows)
                if rebalanced != slot_limits:
                    slot_limits = rebalanced
                    continue
            break

        facts_dump = facts.model_dump(mode="python")
        effective_summary_rows = _augment_summary_rows(summary_rows, final_deduped_rows, facts_dump)
        edge_summary = _format_edge_summary(effective_summary_rows)
        evidence_paths = _build_evidence_paths(final_deduped_rows)

        final_slot_limits = dict(slot_limits)
        raw_overrides = dict(k_slots) if k_slots else {}
        try:
            slot_overrides_clean = _sanitise_slot_values(raw_overrides) if raw_overrides else {}
        except ValueError:
            slot_overrides_clean = {}
        slot_meta = {
            "requested_k": max(int(k_raw), 0),
            "applied_k": max(int(k_value), 0),
            "slot_source": "overrides" if slot_overrides_clean else "auto",
            "requested_overrides": slot_overrides_clean,
            "allocated_total": sum(max(int(final_slot_limits.get(key, 0)), 0) for key in _PATH_SLOT_KEYS),
        }
        if raw_overrides and slot_overrides_clean != raw_overrides:
            slot_meta["requested_overrides_raw"] = raw_overrides

        return ContextPack(
            edge_summary=edge_summary,
            evidence_paths=evidence_paths,
            facts=facts,
            slot_limits=final_slot_limits,
            slot_meta=slot_meta,
        )


__all__ = [
    "json_dumps_safe",
    "GraphContextBuilder",
    "ContextPack",
    "ContextPackBuilder",
    "ContextFacts",
    "EvidencePath",
]
