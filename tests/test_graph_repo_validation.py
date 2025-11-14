from __future__ import annotations

import pytest

from services.graph_repo import GraphRepo


def _base_payload() -> dict:
    return {
        "case_id": "CASE_IMG_CANONICAL",
        "image": {"image_id": "IMG_CANONICAL"},
        "report": {
            "id": "RPT_CANONICAL",
            "text": "Canonical report",
            "model": "dummy-llm",
            "conf": 0.9,
        },
        "findings": [
            {
                "id": "FIND-CANONICAL",
                "type": "Mass",
                "location": "Right lobe of the liver",
                "conf": 0.85,
                "size_cm": 3.2,
            }
        ],
    }


def _build_repo() -> GraphRepo:
    # Bypass __init__ to avoid requiring a live Neo4j driver; prepare_upsert_parameters
    # does not depend on the driver connection.
    return GraphRepo.__new__(GraphRepo)  # type: ignore[misc]


def test_prepare_upsert_parameters_accepts_canonical_values() -> None:
    repo = _build_repo()
    payload = _base_payload()
    prepared = GraphRepo.prepare_upsert_parameters(repo, payload)

    finding = prepared["findings"][0]
    assert finding["type"] == "Mass"
    assert finding["location"] == "Right lobe of the liver"


def test_prepare_upsert_parameters_rejects_noncanonical_type() -> None:
    repo = _build_repo()
    payload = _base_payload()
    payload["findings"][0]["type"] = "mass"

    with pytest.raises(ValueError, match=r"finding\[0\]\.type"):
        GraphRepo.prepare_upsert_parameters(repo, payload)


def test_prepare_upsert_parameters_rejects_noncanonical_location() -> None:
    repo = _build_repo()
    payload = _base_payload()
    payload["findings"][0]["location"] = "right hepatic lobe"

    with pytest.raises(ValueError, match=r"finding\[0\]\.location"):
        GraphRepo.prepare_upsert_parameters(repo, payload)


def test_segments_to_triples_formats_nodes() -> None:
    segments = [
        {
            "source": {"labels": ["Image"], "image_id": "IMG900"},
            "rel": "HAS_FINDING",
            "target": {"labels": ["Finding"], "id": "F900"},
        }
    ]
    triples = GraphRepo._segments_to_triples(segments)
    assert triples == ["Image[IMG900] -HAS_FINDING-> Finding[F900]"]


def test_normalise_path_row_uses_segments_when_triples_missing() -> None:
    row = {
        "slot": "findings",
        "label": "Mass",
        "score": 0.8,
        "segments": [
            {
                "source": {"labels": ["Image"], "image_id": "IMG123"},
                "rel": "HAS_FINDING",
                "target": {"labels": ["Finding"], "id": "F123"},
            }
        ],
    }
    normalised = GraphRepo._normalise_path_row(row)
    assert normalised["triples"] == ["Image[IMG123] -HAS_FINDING-> Finding[F123]"]
