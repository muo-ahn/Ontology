"""Utility helpers for lightweight de-duplication logic."""

from __future__ import annotations

from typing import Dict, List, Tuple

__all__ = ["dedup_findings", "dedup_paths"]


def dedup_findings(fs: List[Dict]) -> List[Dict]:
    """Remove duplicate findings based on semantic attributes."""

    seen: set[Tuple[str, str, float]] = set()
    out: List[Dict] = []
    for finding in fs or []:
        finding = dict(finding)
        f_type = (finding.get("type") or "").strip().lower()
        location = (finding.get("location") or "").strip().lower()
        try:
            size = round(float(finding.get("size_cm")), 1)
        except Exception:
            size = 0.0
        key = (f_type, location, size)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def dedup_paths(paths: List[Dict]) -> List[Dict]:
    """Remove duplicate context paths using label + triple signature."""

    seen: set[Tuple[str, Tuple[str, ...]]] = set()
    out: List[Dict] = []
    for path in paths or []:
        label = path.get("label")
        triples = tuple(path.get("triples") or [])
        sig = (label, triples)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(path)
    return out
