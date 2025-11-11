"""
Consensus module extracted from the /pipeline/analyze router.

Encapsulates all scoring heuristics so callers can focus on providing mode
outputs plus optional weighting/anchor hints.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

CONSENSUS_AGREEMENT_THRESHOLD = 0.6
CONSENSUS_HIGH_CONFIDENCE_THRESHOLD = 0.8
CONSENSUS_MODE_PRIORITY: Tuple[str, ...] = ("VGL", "VL", "V")

TEXT_SIMILARITY_WEIGHT = 0.6
STRUCTURED_OVERLAP_WEIGHT = 0.3
GRAPH_EVIDENCE_WEIGHT = 0.10

BANNED_BY_MODALITY: Dict[str, List[str]] = {
    "US": ["gestational", "fetal", "uterus", "ecg"],
    "CT": ["fetal", "uterus", "ecg"],
}

__all__ = [
    "compute_consensus",
    "normalise_for_consensus",
    "modality_penalty",
    "BANNED_BY_MODALITY",
]


def normalise_for_consensus(text: str) -> str:
    """Lowercase and squeeze whitespace to normalise free-form text."""

    return " ".join(text.lower().split())


def _jaccard_similarity(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _preferred_mode(modes: Sequence[str]) -> Optional[str]:
    for mode in CONSENSUS_MODE_PRIORITY:
        if mode in modes:
            return mode
    return modes[0] if modes else None


def _normalise_term(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        candidate = str(value)
    else:
        candidate = str(value).strip()
    cleaned = " ".join(candidate.split()).strip().lower()
    return cleaned or None


def _expand_term(term: str) -> Set[str]:
    variants: Set[str] = {term}
    if " " in term:
        for token in term.split():
            token_clean = token.strip()
            if len(token_clean) >= 4:
                variants.add(token_clean)
    return variants


def _collect_finding_terms(findings: Optional[List[Dict[str, Any]]]) -> Tuple[Set[str], Set[str]]:
    type_terms: Set[str] = set()
    location_terms: Set[str] = set()
    if not findings:
        return type_terms, location_terms
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        type_term = _normalise_term(finding.get("type"))
        if type_term:
            type_terms.update(_expand_term(type_term))
        location_term = _normalise_term(finding.get("location"))
        if location_term:
            location_terms.update(_expand_term(location_term))
    return type_terms, location_terms


def _term_overlap_score(text_lower: str, terms: Set[str]) -> float:
    if not text_lower or not terms:
        return 0.0
    hits = 0
    total = 0
    for term in terms:
        if not term:
            continue
        total += 1
        if term in text_lower:
            hits += 1
    if total == 0:
        return 0.0
    return min(1.0, hits / total)


def _structured_overlap_score(text: str, type_terms: Set[str], location_terms: Set[str]) -> float:
    if not text:
        return 0.0
    lowered = text.lower()
    type_score = _term_overlap_score(lowered, type_terms)
    location_score = _term_overlap_score(lowered, location_terms)
    weighted = (type_score * 0.6) + (location_score * 0.4)
    return min(1.0, weighted)


def modality_penalty(text: str, modality: Optional[str]) -> float:
    """Return a negative penalty if the text conflicts with the study modality."""

    if not modality:
        return 0.0

    t = (text or "").lower()
    bad = BANNED_BY_MODALITY.get(modality.upper(), [])
    return -0.2 if any(term in t for term in bad) else 0.0


def compute_consensus(
    results: Dict[str, Dict[str, Any]],
    modality: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
    min_agree: Optional[float] = None,
    *,
    anchor_mode: Optional[str] = None,
    anchor_min_score: float = 0.75,
    structured_findings: Optional[List[Dict[str, Any]]] = None,
    graph_paths_strength: float = 0.0,
) -> Dict[str, Any]:
    weight_map = {k: float(v) for k, v in (weights or {}).items()}
    fallback_threshold = min_agree if min_agree is not None else CONSENSUS_AGREEMENT_THRESHOLD
    modality_key = (modality or "").upper()
    penalised_modes: set[str] = set()
    type_terms, location_terms = _collect_finding_terms(structured_findings)
    graph_signal = max(0.0, min(1.0, float(graph_paths_strength or 0.0)))

    available: Dict[str, Dict[str, Any]] = {}
    for mode, payload in results.items():
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            lowered = text.lower()
            banned_terms = BANNED_BY_MODALITY.get(modality_key, []) if modality_key else []
            offending_terms = [term for term in banned_terms if term in lowered]
            penalty = modality_penalty(text, modality_key)
            if penalty < 0:
                penalised_modes.add(mode)
            base_weight = weight_map.get(mode, 1.0)
            effective_weight = max(base_weight + penalty, 0.0)
            available[mode] = {
                "text": text,
                "normalised": normalise_for_consensus(text),
                "latency_ms": payload.get("latency_ms"),
                "degraded": payload.get("degraded"),
                "penalty": penalty,
                "penalty_terms": offending_terms,
                "effective_weight": effective_weight,
                "base_weight": base_weight,
                "structured_overlap": _structured_overlap_score(text, type_terms, location_terms),
            }

    total_modes = len(available)
    if total_modes == 0:
        return {
            "text": "",
            "status": "empty",
            "supporting_modes": [],
            "disagreed_modes": [],
            "agreement_score": 0.0,
            "confidence": "low",
        }

    if total_modes == 1:
        sole_mode = next(iter(available.keys()))
        data = available[sole_mode]
        return {
            "text": data["text"],
            "status": "single",
            "supporting_modes": [sole_mode],
            "disagreed_modes": [],
            "agreement_score": 1.0,
            "confidence": "medium",
        }

    best_pair: Optional[tuple[str, str]] = None
    best_pair_weight = 1.0
    best_weighted_score = -1.0
    best_raw_score = 0.0
    best_pair_penalty_modes: tuple[str, ...] = ()
    best_pair_graph_bonus = False
    for (mode_a, data_a), (mode_b, data_b) in combinations(available.items(), 2):
        score = _jaccard_similarity(data_a["normalised"], data_b["normalised"])
        weight_a = data_a.get("effective_weight", weight_map.get(mode_a, 1.0))
        weight_b = data_b.get("effective_weight", weight_map.get(mode_b, 1.0))
        pair_weight = max(weight_a + weight_b, 0.0) / 2.0
        penalty_adjustment = (
            min(data_a.get("penalty", 0.0), 0.0) + min(data_b.get("penalty", 0.0), 0.0)
        ) / 2.0
        structure_bonus = (
            data_a.get("structured_overlap", 0.0) + data_b.get("structured_overlap", 0.0)
        ) / 2.0
        pair_has_vgl = "VGL" in (mode_a, mode_b)
        graph_bonus = GRAPH_EVIDENCE_WEIGHT * graph_signal if pair_has_vgl else 0.0
        text_component = score * TEXT_SIMILARITY_WEIGHT
        structure_component = structure_bonus * STRUCTURED_OVERLAP_WEIGHT
        raw_score = text_component + structure_component + graph_bonus
        adjusted_score = min(max(raw_score + penalty_adjustment, 0.0), 1.0)
        weighted_score = adjusted_score * pair_weight
        if weighted_score > best_weighted_score:
            best_weighted_score = weighted_score
            best_pair = (mode_a, mode_b)
            best_raw_score = adjusted_score
            best_pair_weight = pair_weight
            best_pair_penalty_modes = tuple(
                sorted({candidate for candidate in (mode_a, mode_b) if available[candidate]["penalty"] < 0})
            )
            best_pair_graph_bonus = graph_bonus > 0

    agreement_score = max(best_raw_score, 0.0)
    supporting_modes: List[str] = []
    fallback_used = False
    if best_pair:
        effective_threshold = CONSENSUS_AGREEMENT_THRESHOLD
        if agreement_score >= effective_threshold:
            supporting_modes = sorted(
                best_pair,
                key=lambda mode: CONSENSUS_MODE_PRIORITY.index(mode)
                if mode in CONSENSUS_MODE_PRIORITY
                else len(CONSENSUS_MODE_PRIORITY),
            )
        elif agreement_score >= fallback_threshold and best_pair_weight > 1.0:
            supporting_modes = sorted(
                best_pair,
                key=lambda mode: CONSENSUS_MODE_PRIORITY.index(mode)
                if mode in CONSENSUS_MODE_PRIORITY
                else len(CONSENSUS_MODE_PRIORITY),
            )
            fallback_used = True

    penalty_note: Optional[str] = None
    anchor_mode_used = False
    if not supporting_modes and anchor_mode and anchor_mode in available:
        anchor_data = available[anchor_mode]
        degraded_marker = anchor_data.get("degraded")
        if not degraded_marker:
            supporting_modes = [anchor_mode]
            anchor_mode_used = True
            agreement_score = max(agreement_score, anchor_min_score)

    if supporting_modes:
        conflicted = [mode for mode in supporting_modes if available[mode].get("penalty", 0.0) < 0]
        if conflicted:
            penalty_note = "modality conflict: " + ", ".join(sorted(conflicted))
            supporting_modes = [mode for mode in supporting_modes if available[mode]["penalty"] >= 0]
    elif best_pair_penalty_modes:
        penalty_note = "modality conflict: " + ", ".join(best_pair_penalty_modes)

    notes: Optional[str] = None
    if supporting_modes:
        preferred = _preferred_mode(supporting_modes) or supporting_modes[0]
        consensus_text = available[preferred]["text"]
        status = "agree"
        if anchor_mode_used:
            confidence = "high" if agreement_score >= CONSENSUS_HIGH_CONFIDENCE_THRESHOLD else "medium"
            notes = "graph-grounded mode dominated consensus"
        elif agreement_score >= CONSENSUS_HIGH_CONFIDENCE_THRESHOLD:
            confidence = "high"
            notes = "agreement across requested modes"
        elif fallback_used:
            confidence = "medium"
            notes = "weighted agreement favouring grounded evidence"
        else:
            confidence = "medium"
            notes = "agreement across requested modes"
    else:
        preferred = _preferred_mode(list(available.keys())) or next(iter(available.keys()))
        consensus_text = available[preferred]["text"]
        confidence = "low"
        status = "disagree"
        supporting_modes = [preferred] if preferred else []
        notes = "outputs diverged across modes"
        if available.get(preferred, {}).get("penalty", 0.0) < 0:
            terms = available.get(preferred, {}).get("penalty_terms", [])
            detail_terms = ", ".join(sorted(set(terms))) if terms else "unexpected content"
            penalty_detail = f"penalised terms: {detail_terms}"
            penalty_note = f"{penalty_note} | {penalty_detail}" if penalty_note else penalty_detail
            confidence = "very_low"

    disagreed_modes = sorted(set(available.keys()) - set(supporting_modes))
    degraded_inputs = sorted(mode for mode, data in available.items() if data.get("degraded"))
    presented_text = consensus_text if status != "disagree" else f"낮은 확신: {consensus_text}"

    consensus_payload: Dict[str, Any] = {
        "text": consensus_text,
        "presented_text": presented_text,
        "status": status,
        "supporting_modes": supporting_modes,
        "disagreed_modes": disagreed_modes,
        "agreement_score": round(agreement_score, 3),
        "confidence": confidence,
        "evaluated_modes": sorted(available.keys()),
    }
    if degraded_inputs:
        consensus_payload["degraded_inputs"] = degraded_inputs
    all_notes: List[str] = []
    if notes:
        all_notes.append(notes)
    if penalty_note:
        all_notes.append(penalty_note)
    if status != "disagree":
        structured_alignment = any(
            available.get(mode, {}).get("structured_overlap", 0.0) >= 0.5 for mode in supporting_modes
        )
        if structured_alignment:
            all_notes.append("structured finding terms aligned across agreeing modes")
        if graph_signal > 0 and ("VGL" in supporting_modes or best_pair_graph_bonus):
            all_notes.append(f"graph evidence boosted consensus (paths_signal={graph_signal:.2f})")
    if penalised_modes and status != "disagree" and not penalty_note:
        all_notes.append("penalty applied for modality conflict")
    if all_notes:
        consensus_payload["notes"] = " | ".join(all_notes)

    return consensus_payload
