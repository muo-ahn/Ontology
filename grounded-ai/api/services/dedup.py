"""De-duplication helpers for findings and graph paths."""

from __future__ import annotations

from typing import Dict, List, Tuple

__all__ = ["dedup_findings", "dedup_paths"]


def dedup_findings(findings: List[Dict]) -> List[Dict]:
    """Remove duplicate findings based on semantic attributes."""

    seen: set[Tuple[str, str, float]] = set()
    deduped: List[Dict] = []
    for finding in findings or []:
        finding_copy = dict(finding)
        finding_type = (finding_copy.get("type") or "").strip().lower()
        location = (finding_copy.get("location") or "").strip().lower()
        try:
            size = round(float(finding_copy.get("size_cm")), 1)
        except Exception:
            size = 0.0
        signature = (finding_type, location, size)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(finding_copy)
    return deduped


def dedup_paths(paths: List[Dict]) -> List[Dict]:
    """Remove duplicate context paths using label + triple signature."""

    seen: set[Tuple[str, Tuple[str, ...]]] = set()
    deduped: List[Dict] = []
    for path in paths or []:
        label = path.get("label")
        triples = tuple(path.get("triples") or [])
        signature = (label, triples)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(path)
    return deduped
