"""Canonical label/location mappings shared across the pipeline."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple


def _simplify(value: str) -> str:
    """Normalise strings for case-insensitive, punctuation-free comparison."""

    normalised = unicodedata.normalize("NFKD", value).lower()
    normalised = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9가-힣]+", "", normalised)


LABEL_CANONICALS: Dict[str, Dict[str, Iterable[str]]] = {
    "Mass": {
        "aliases": ["lesion", "덩어리", "mass lesion"],
    },
    "Nodule": {
        "aliases": ["결절", "nodule", "small mass"],
    },
    "Opacity": {
        "aliases": ["infiltrate", "음영", "opacity"],
    },
    "Hypodensity": {
        "aliases": ["low attenuation area", "저음영", "reduced density"],
    },
    "Subarachnoid Hemorrhage": {
        "aliases": ["sah", "subarachnoid bleeding", "수막하출혈", "subarachnoid haemorrhage"],
    },
    "Ischemic": {
        "aliases": ["ischemia", "ischemic change"],
    },
}

LOCATION_CANONICALS: Dict[str, Dict[str, Iterable[str]]] = {
    "Right lobe of the liver": {
        "aliases": ["right hepatic lobe", "rhl", "right lobe liver"],
    },
    "Left parietal lobe": {
        "aliases": ["left parietal region", "left parietal", "좌측두정엽"],
    },
    "Right middle lobe": {
        "aliases": ["rml", "right middle lung lobe"],
    },
    "Lung": {
        "aliases": ["pulmonary", "lungs"],
    },
    "Liver": {
        "aliases": ["hepatic parenchyma", "liver"],
    },
}

TIEBREAKER_PRIORITY: List[str] = [
    "Subarachnoid Hemorrhage",
    "Hypodensity",
    "Mass",
    "Nodule",
    "Opacity",
    "Ischemic",
]


def _build_alias_map(table: Dict[str, Dict[str, Iterable[str]]]) -> Dict[str, Tuple[str, str]]:
    mapping: Dict[str, Tuple[str, str]] = {}
    for canonical, meta in table.items():
        simplified = _simplify(canonical)
        mapping.setdefault(simplified, (canonical, "canonical"))
        for alias in meta.get("aliases") or []:
            alias_clean = alias.strip()
            if not alias_clean:
                continue
            mapping[_simplify(alias_clean)] = (canonical, f"alias:{alias_clean}")
    return mapping


_LABEL_ALIAS_MAP = _build_alias_map(LABEL_CANONICALS)
_LOCATION_ALIAS_MAP = _build_alias_map(LOCATION_CANONICALS)
_TIEBREAKER_MAP = {label: idx for idx, label in enumerate(TIEBREAKER_PRIORITY)}


def canonicalise_label(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    candidate = raw.strip()
    if not candidate:
        return None, None
    simplified = _simplify(candidate)
    match = _LABEL_ALIAS_MAP.get(simplified)
    if match:
        return match
    return candidate, "unchanged"


def canonicalise_location(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    candidate = raw.strip()
    if not candidate:
        return None, None
    simplified = _simplify(candidate)
    match = _LOCATION_ALIAS_MAP.get(simplified)
    if match:
        return match
    return candidate, "unchanged"


def rank_label(label: Optional[str]) -> int:
    if not label:
        return len(TIEBREAKER_PRIORITY) + 1
    return _TIEBREAKER_MAP.get(label, len(TIEBREAKER_PRIORITY) + 1)


__all__ = [
    "LABEL_CANONICALS",
    "LOCATION_CANONICALS",
    "TIEBREAKER_PRIORITY",
    "canonicalise_label",
    "canonicalise_location",
    "rank_label",
]
