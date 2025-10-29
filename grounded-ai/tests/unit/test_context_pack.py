from __future__ import annotations

import json
from copy import deepcopy
from typing import Dict, List

import pytest

from services.context_pack import ContextPackBuilder, GraphContextBuilder


class DummyRepo:
    """In-memory stand-in for GraphRepo to drive context builders."""

    def __init__(
        self,
        bundle: Dict[str, object],
        paths_by_k: Dict[int, List[Dict[str, object]]],
        fallback_paths: List[Dict[str, object]] | None = None,
    ) -> None:
        self._bundle = bundle
        self._paths_by_k = paths_by_k
        self._fallback_paths = fallback_paths or []
        self.bundle_calls: List[str] = []
        self.path_calls: List[int] = []

    def query_bundle(self, image_id: str) -> Dict[str, object]:
        self.bundle_calls.append(image_id)
        return deepcopy(self._bundle)

    def query_paths(self, image_id: str, k: int) -> List[Dict[str, object]]:
        self.path_calls.append(k)
        payload = deepcopy(self._paths_by_k.get(k, self._fallback_paths))
        return payload


def _base_bundle(image_id: str = "IMG_123") -> Dict[str, object]:
    return {
        "summary": [{"rel": "HAS_FINDING", "cnt": 2, "avg_conf": 0.8}],
        "facts": {
            "image_id": image_id,
            "findings": [
                {"id": "F1", "type": "nodule", "location": "RUL"},
                {"id": "F2", "type": "atelectasis", "location": "LLL"},
            ],
        },
    }


def _paths_payload(long_tail: bool = False) -> Dict[int, List[Dict[str, object]]]:
    base_path = {
        "label": "Nodule @ Right upper lobe",
        "triples": [
            "Image[IMG_123] -HAS_FINDING-> Finding[F1]",
            "Finding[F1] -LOCATED_IN-> Anatomy[Lung-RUL]",
        ],
        "score": 0.91,
    }
    if not long_tail:
        return {2: [base_path], 1: [base_path]}

    long_triple = "Image[IMG_123] -HAS_FINDING-> Finding[F2] " + ("." * 180)
    extended_path = {
        "label": "Atelectasis @ Lower lobe",
        "triples": [
            long_triple,
        ],
        "score": 0.77,
    }
    return {
        2: [base_path, extended_path],
        1: [base_path],
        0: [],
    }


def test_build_bundle_includes_evidence_paths():
    repo = DummyRepo(bundle=_base_bundle(), paths_by_k=_paths_payload())
    builder = GraphContextBuilder(repo=repo)

    bundle = builder.build_bundle("IMG_123", k=2)

    assert repo.bundle_calls == ["IMG_123"]
    assert repo.path_calls == [2]
    assert bundle["summary"][0] == "[EDGE SUMMARY]"
    assert bundle["paths"]
    assert bundle["paths"][0]["label"] == "Nodule @ Right upper lobe"
    assert bundle["paths"][0]["triples"][0] == "Image[IMG_123] -HAS_FINDING-> Finding[F1]"
    assert bundle["facts"]["image_id"] == "IMG_123"
    assert len(bundle["facts"]["findings"]) == 2


def test_build_bundle_reduces_k_when_context_too_long():
    repo = DummyRepo(bundle=_base_bundle(), paths_by_k=_paths_payload(long_tail=True))
    builder = GraphContextBuilder(repo=repo)

    bundle = builder.build_bundle("IMG_123", k=2, max_chars=260)

    assert repo.path_calls == [2, 1, 0]
    assert repo.path_calls.count(1) == 1
    assert len(bundle["paths"]) <= 1
    if bundle["paths"]:
        assert bundle["paths"][0]["label"] == "Nodule @ Right upper lobe"


def test_build_prompt_context_json_mode_skips_path_fetch():
    repo = DummyRepo(bundle=_base_bundle(), paths_by_k=_paths_payload())
    builder = GraphContextBuilder(repo=repo)

    context_json = builder.build_prompt_context("IMG_123", mode="json")

    assert repo.path_calls == []
    facts = json.loads(context_json)
    assert facts["image_id"] == "IMG_123"
    assert len(facts["findings"]) == 2


def test_build_prompt_context_invalid_mode_raises():
    repo = DummyRepo(bundle=_base_bundle(), paths_by_k=_paths_payload())
    builder = GraphContextBuilder(repo=repo)

    with pytest.raises(ValueError):
        builder.build_prompt_context("IMG_123", mode="markdown")


def test_context_pack_builder_returns_dataclass():
    repo = DummyRepo(bundle=_base_bundle(), paths_by_k=_paths_payload())
    builder = ContextPackBuilder(repo=repo, top_k_paths=2)

    pack = builder.build("IMG_123")

    assert "HAS_FINDING" in pack.edge_summary
    assert len(pack.evidence_paths) == 1
    assert pack.evidence_paths[0].label == "Nodule @ Right upper lobe"
    assert pack.facts.image_id == "IMG_123"
