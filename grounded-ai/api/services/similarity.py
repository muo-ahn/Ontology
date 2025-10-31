"""Helpers for computing lightweight similarity between image nodes."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _normalise_token(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    token = token.strip("_")
    return token or None


def _collect_tokens(values: Iterable[Optional[str]]) -> set[str]:
    return {token for token in (_normalise_token(v) for v in values) if token}


def _extract_finding_tokens(findings: Sequence[Dict[str, Any]]) -> Tuple[set[str], set[str]]:
    types: set[str] = set()
    locations: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        types |= _collect_tokens([finding.get("type")])
        locations |= _collect_tokens([finding.get("location")])
    return types, locations


def compute_similarity_scores(
    *,
    new_image: Dict[str, Any],
    candidates: Sequence[Dict[str, Any]],
    threshold: float = 0.5,
    top_k: int = 10,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Return (edges, summary) for candidates whose similarity score meets the threshold.

    The summary list is intended for API payloads, while the edges list is suitable
    for Neo4j upserts. Both lists are sorted by score (descending) then image_id.
    """

    modality = (new_image.get("modality") or "").strip().upper()
    findings = new_image.get("findings")
    new_types: set[str]
    new_locations: set[str]
    if isinstance(findings, Sequence):
        new_types, new_locations = _extract_finding_tokens(findings)
    else:
        new_types, new_locations = set(), set()

    new_semantic_tokens = new_types | new_locations

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        candidate_id = candidate.get("image_id")
        if not candidate_id:
            continue

        c_modality = (candidate.get("modality") or "").strip().upper()
        modality_match = 1.0 if modality and modality == c_modality else 0.0

        candidate_types = _collect_tokens(candidate.get("finding_types") or [])
        candidate_locations = _collect_tokens(candidate.get("finding_locations") or [])
        candidate_anatomy = _collect_tokens(candidate.get("anatomy_codes") or [])
        candidate_semantic_tokens = candidate_types | candidate_locations | candidate_anatomy

        semantic_components: List[str] = []
        if new_types & candidate_types:
            semantic_components.append("finding_type")
        if new_locations & candidate_locations:
            semantic_components.append("location")
        if new_semantic_tokens & candidate_anatomy:
            semantic_components.append("anatomy")

        semantics_match = 1.0 if semantic_components else 0.0
        score = round((0.6 * modality_match) + (0.4 * semantics_match), 3)
        if score < threshold:
            continue

        basis_parts: List[str] = []
        if modality_match:
            basis_parts.append("modality")
        if semantic_components:
            basis_parts.extend(semantic_components)

        scored.append(
            (
                score,
                {
                    "image_id": candidate_id,
                    "score": score,
                    "basis_parts": basis_parts or ["none"],
                },
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1]["image_id"]))
    top_scored = scored[:top_k]

    summary = [{"id": item["image_id"], "score": item["score"]} for _, item in top_scored]
    edges = [
        {"image_id": item["image_id"], "score": item["score"], "basis": "+".join(item["basis_parts"])}
        for _, item in top_scored
    ]

    return edges, summary


__all__ = ["compute_similarity_scores"]
