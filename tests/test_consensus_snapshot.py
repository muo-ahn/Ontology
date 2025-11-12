from __future__ import annotations

import pytest

from services.consensus import compute_consensus


def _sample_results() -> dict[str, dict[str, object]]:
    return {
        "V": {
            "text": "Ultrasound caption suggests early pregnancy sac in uterus.",
            "latency_ms": 10,
        },
        "VL": {
            "text": "Caption + report emphasize gestational sac without hepatic lesions.",
            "latency_ms": 12,
        },
        "VGL": {
            "text": "Graph evidence: Right hepatic mass with vascular invasion near portal vein.",
            "latency_ms": 14,
        },
    }


def test_compute_consensus_prefers_graph_mode_snapshot():
    results = _sample_results()
    findings = [
        {"type": "Mass", "location": "Right lobe of the liver"},
        {"type": "Vascular invasion", "location": "Portal vein"},
    ]

    consensus = compute_consensus(
        results,
        weights={"V": 1.0, "VL": 1.2, "VGL": 1.8},
        structured_findings=findings,
        graph_paths_strength=0.45,
        min_agree=0.35,
        anchor_mode="VGL",
    )

    assert consensus["text"] == results["VGL"]["text"]
    assert consensus["supporting_modes"] == ["VGL"]
    assert consensus["disagreed_modes"] == ["V", "VL"]
    assert consensus["status"] == "agree"
    assert consensus["confidence"] == "medium"
    assert consensus["agreement_score"] == pytest.approx(0.75, rel=0.05)
    assert "graph evidence" in (consensus.get("notes") or "")
    assert "mode_weights" in consensus and consensus["mode_weights"]["VGL"] > consensus["mode_weights"]["V"]
    components = consensus.get("agreement_components") or {}
    assert components.get("graph", 0) > 0
