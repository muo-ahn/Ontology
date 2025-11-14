from __future__ import annotations

from typing import Dict, List

from services.consensus import compute_consensus


def _base_results() -> Dict[str, Dict[str, str]]:
    return {
        "V": {"text": "Ultrasound shows a stable hepatic lesion without new hemorrhage."},
        "VL": {"text": "Hepatic lesion remains stable with no new bleeding noted."},
        "VGL": {"text": "Graph evidence: stable liver lesion corroborated by prior study."},
    }


def test_compute_consensus_graph_bonus_improves_agreement() -> None:
    structured: List[Dict[str, str]] = [{"type": "lesion", "location": "liver"}]
    baseline = compute_consensus(
        _base_results(),
        weights={"V": 1.0, "VL": 1.1, "VGL": 1.8},
        min_agree=0.35,
        anchor_mode=None,
        structured_findings=structured,
        graph_paths_strength=0.0,
    )
    boosted = compute_consensus(
        _base_results(),
        weights={"V": 1.0, "VL": 1.1, "VGL": 1.8},
        min_agree=0.35,
        anchor_mode=None,
        structured_findings=structured,
        graph_paths_strength=0.9,
    )
    assert boosted["agreement_score"] > baseline["agreement_score"]
    anchored = compute_consensus(
        _base_results(),
        weights={"V": 1.0, "VL": 1.1, "VGL": 1.8},
        min_agree=0.35,
        anchor_mode="VGL",
        anchor_min_score=0.75,
        structured_findings=structured,
        graph_paths_strength=0.9,
    )
    assert anchored["status"] == "agree"
    assert anchored["confidence"] in {"medium", "high"}
    assert "graph evidence" in (anchored.get("notes") or "").lower()
    assert anchored.get("vote_summary", {}).get("support", 0) >= 2


def test_compute_consensus_structured_terms_raise_score() -> None:
    results = {
        "V": {"text": "Ultrasound outlines an indeterminate liver lesion near the dome."},
        "VGL": {"text": "Graph evidence: liver lesion persists adjacent to the portal vein."},
    }
    no_struct = compute_consensus(
        results,
        weights={"V": 1.0, "VGL": 1.4},
        min_agree=0.35,
        anchor_mode=None,
        structured_findings=[],
        graph_paths_strength=0.0,
    )
    with_struct = compute_consensus(
        results,
        weights={"V": 1.0, "VGL": 1.4},
        min_agree=0.35,
        anchor_mode=None,
        structured_findings=[{"type": "lesion", "location": "liver"}],
        graph_paths_strength=0.0,
    )
    assert no_struct["status"] == "disagree"
    assert with_struct["agreement_score"] > no_struct["agreement_score"]
    assert with_struct["status"] == "agree"
    assert "structured finding terms" in (with_struct.get("notes") or "").lower()
    assert "mode_weights" in with_struct
    assert with_struct.get("vote_summary", {}).get("support") >= 2


def test_consensus_reports_conflict_modes_when_penalised() -> None:
    results = {
        "V": {"text": "Ultrasound suggests fetal heartbeat."},
        "VGL": {"text": "Graph evidence: liver lesion persists adjacent to the portal vein."},
    }
    consensus = compute_consensus(
        results,
        modality="CT",
        weights={"V": 1.0, "VGL": 1.4},
        structured_findings=[{"type": "lesion", "location": "liver"}],
        graph_paths_strength=0.4,
    )
    assert "conflict_modes" in consensus
    assert "V" in consensus["conflict_modes"]


def test_consensus_marks_limited_support_with_vote_summary() -> None:
    results = {
        "V": {"text": "Brain MRI shows diffuse ischemia without abdominal findings."},
        "VL": {"text": "Chest study remains negative for pulmonary nodules."},
        "VGL": {"text": "Right lobe liver mass persists measuring 5 cm on graph evidence."},
    }
    consensus = compute_consensus(
        results,
        weights={"V": 1.0, "VL": 1.0, "VGL": 1.8},
        min_agree=0.35,
        anchor_mode="VGL",
        structured_findings=[{"type": "Mass", "location": "Right lobe of the liver"}],
        graph_paths_strength=0.25,
    )
    votes = consensus.get("vote_summary") or {}
    assert votes == {"support": 1, "against": 2, "total": 3}
    assert consensus["status"] == "agree"
    assert consensus["confidence"] == "low"
    assert "limited mode agreement" in (consensus.get("notes") or "")
