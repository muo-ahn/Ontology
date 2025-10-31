from __future__ import annotations

import sys
import types

if "py2neo" not in sys.modules:
    mock_py2neo = types.ModuleType("py2neo")
    mock_py2neo.Graph = object
    mock_py2neo.Node = object
    mock_py2neo.Relationship = object
    mock_py2neo.errors = types.SimpleNamespace(ClientError=Exception)
    sys.modules["py2neo"] = mock_py2neo
    mock_py2neo_errors = types.ModuleType("py2neo.errors")
    mock_py2neo_errors.ClientError = Exception
    sys.modules["py2neo.errors"] = mock_py2neo_errors

if "neo4j" not in sys.modules:
    mock_neo4j = types.ModuleType("neo4j")
    mock_neo4j.GraphDatabase = types.SimpleNamespace(driver=lambda *args, **kwargs: object())
    sys.modules["neo4j"] = mock_neo4j
    mock_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    mock_neo4j_exceptions.Neo4jError = Exception
    sys.modules["neo4j.exceptions"] = mock_neo4j_exceptions

from services.similarity import compute_similarity_scores


def test_compute_similarity_scores_returns_sorted_edges() -> None:
    new_image = {
        "modality": "US",
        "findings": [
            {"type": "mass", "location": "liver"},
            {"type": "nodule", "location": "lung"},
        ],
    }
    candidates = [
        {
            "image_id": "IMG200",
            "modality": "US",
            "finding_types": ["mass"],
            "finding_locations": [],
            "anatomy_codes": [],
        },
        {
            "image_id": "IMG150",
            "modality": "CT",
            "finding_types": ["ischemic"],
            "finding_locations": [],
            "anatomy_codes": [],
        },
        {
            "image_id": "IMG101",
            "modality": "US",
            "finding_types": ["nodule"],
            "finding_locations": ["lung"],
            "anatomy_codes": ["an_lung"],
        },
    ]

    edges, summary = compute_similarity_scores(
        new_image=new_image,
        candidates=candidates,
        threshold=0.5,
        top_k=10,
    )

    assert [edge["image_id"] for edge in edges] == ["IMG101", "IMG200"]
    assert summary == [{"id": "IMG101", "score": 1.0}, {"id": "IMG200", "score": 1.0}]
    assert edges[0]["basis"].startswith("modality")
    assert "finding_type" in edges[0]["basis"]
    assert "finding_type" in edges[1]["basis"]


def test_similarity_scores_respect_threshold_and_limit() -> None:
    new_image = {
        "modality": "XR",
        "findings": [{"type": "opacity"}],
    }
    candidates = [
        {"image_id": f"IMG{i:03d}", "modality": "XR", "finding_types": ["opacity"], "finding_locations": [], "anatomy_codes": []}
        for i in range(20)
    ]

    edges, summary = compute_similarity_scores(
        new_image=new_image,
        candidates=candidates,
        threshold=0.5,
        top_k=5,
    )

    assert len(edges) == 5
    assert len(summary) == 5
    assert all(item["score"] >= 1.0 - 1e-6 for item in summary)
