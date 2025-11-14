from __future__ import annotations

import os
import shutil
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.graph_repo import GraphRepo
from services.dummy_registry import LookupResult
from services.context_pack import GraphContextBuilder as RealGraphContextBuilder, GraphContextResult
from routers import pipeline as pipeline_module


SEED_FILE = Path(__file__).resolve().parents[1] / "grounded-ai" / "scripts" / "cyphers" / "seed_dummy_C.cypher"
_SAMPLE_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=="
)


_FINDINGS_FIXTURE: List[Dict[str, Any]] = [
    {
        "id": "C_F_1",
        "type": "mass",
        "location": "liver",
        "size_cm": 2.1,
        "conf": 0.88,
    },
    {
        "id": "C_F_2",
        "type": "nodule",
        "location": "lung",
        "size_cm": 1.4,
        "conf": 0.82,
    },
    {
        "id": "C_F_3",
        "type": "ischemic",
        "location": "liver",
        "size_cm": 1.9,
        "conf": 0.85,
    },
]


class _PipelineHarness:
    def __init__(
        self,
        *,
        lookup: LookupResult,
        paths_by_slot: Dict[str, List[Dict[str, Any]]],
        summary_rows: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
        force_empty_upsert_ids: bool = False,
    ) -> None:
        self.lookup = lookup
        self.paths_by_slot: Dict[str, List[Dict[str, Any]]] = {
            key: [dict(entry) for entry in value] for key, value in paths_by_slot.items()
        }
        self.summary_rows = list(summary_rows or [])
        self.facts = dict(facts or {"image_id": lookup.image_id, "findings": []})
        self.instances: List[object] = []
        self.slot_requests: List[Dict[str, int]] = []
        self.storage_records: Dict[str, set[str]] = {}
        self.force_empty_upsert_ids = force_empty_upsert_ids

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = self

        class StubGraphRepo:
            def __init__(self) -> None:
                self._closed = False
                self._stored_findings: Dict[str, List[str]] = {}

            @classmethod
            def from_env(cls) -> "StubGraphRepo":  # type: ignore[override]
                instance = cls()
                harness.instances.append(instance)
                return instance

            def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                return payload

            def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
                if expected_ids is None:
                    storage = harness.storage_records.get(image_id, set())
                    return list(storage)
                return list(expected_ids)

            def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                image = payload.get("image") or {}
                image_id = str(image.get("image_id") or harness.lookup.image_id)
                storage_uri = image.get("storage_uri")
                bucket = harness.storage_records.setdefault(image_id, set())
                if storage_uri:
                    bucket.add(str(storage_uri))
                stored = [str((finding or {}).get("id") or f"MOCK_{idx}") for idx, finding in enumerate(payload.get("findings") or [])]
                if harness.force_empty_upsert_ids:
                    stored = []
                self._stored_findings[image_id] = stored
                return {"image_id": image_id, "finding_ids": stored}

            def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
                return list(self._stored_findings.get(image_id, []))

            def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                data = deepcopy(payload)
                image = dict(data.get("image") or {})
                image.setdefault("image_id", harness.lookup.image_id)
                data["image"] = image
                data.setdefault("case_id", f"CASE_{harness.lookup.image_id}")
                return data

            def query_bundle(self, image_id: str) -> Dict[str, Any]:
                bundle_facts = dict(harness.facts)
                bundle_facts.setdefault("image_id", image_id)
                bundle_facts.setdefault("findings", [])
                return {"summary": list(harness.summary_rows), "facts": bundle_facts}

            def query_paths(
                self,
                image_id: str,
                k: int = 2,
                *,
                alpha_finding: Optional[float] = None,
                beta_report: Optional[float] = None,
                k_slots: Optional[Dict[str, int]] = None,
            ) -> List[Dict[str, Any]]:
                slots = {key: int(value) for key, value in (k_slots or {}).items()}
                harness.slot_requests.append(slots)
                results: List[Dict[str, Any]] = []
                for slot_key, entries in harness.paths_by_slot.items():
                    budget = max(int(slots.get(slot_key, 0)), 0)
                    if budget <= 0:
                        continue
                    for entry in entries[:budget]:
                        results.append(dict(entry))
                return results

            def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
                return []

            def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
                return 0

            def close(self) -> None:
                self._closed = True

        monkeypatch.setattr(pipeline_module, "GraphRepo", StubGraphRepo)
        monkeypatch.setattr(pipeline_module, "GraphContextBuilder", RealGraphContextBuilder)

        def _resolve_by_path(cls, path: Optional[str]) -> LookupResult:  # type: ignore[override]
            return harness.lookup

        def _resolve_by_id(cls, raw_id: str) -> LookupResult:  # type: ignore[override]
            return harness.lookup

        monkeypatch.setattr(pipeline_module.DummyImageRegistry, "resolve_by_path", classmethod(_resolve_by_path))
        monkeypatch.setattr(pipeline_module.DummyImageRegistry, "resolve_by_id", classmethod(_resolve_by_id))


def _cypher_shell_base_cmd() -> List[str]:
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASS", "test1234")
    uri = os.getenv("NEO4J_URI")
    database = os.getenv("NEO4J_DATABASE")

    cmd = ["cypher-shell", "-u", user, "-p", password]
    if uri:
        cmd.extend(["-a", uri])
    if database:
        cmd.extend(["-d", database])
    return cmd


def _load_seed_data() -> None:
    if not SEED_FILE.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_FILE}")

    base_cmd = _cypher_shell_base_cmd()
    subprocess.run(base_cmd + ["MATCH (n) DETACH DELETE n;"], check=True)
    subprocess.run(base_cmd + ["-f", os.fspath(SEED_FILE)], check=True)


def _upsert_reference_case() -> None:
    repo = GraphRepo.from_env()
    try:
        repo.upsert_case(
            {
                "case_id": "CASE_US001",
                "image": {
                    "image_id": "US001",
                    "path": "/data/dummy/US001.png",
                    "modality": "US",
                },
                "report": {
                    "id": "C_R_1",
                    "text": "Focal hepatic lesion with satellite nodule.",
                    "model": "dummy-llm",
                    "conf": 0.91,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
                "findings": deepcopy(_FINDINGS_FIXTURE),
            }
        )
    finally:
        repo.close()


def test_pipeline_marks_low_confidence_when_graph_evidence_missing(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeGraphRepo:
        def __init__(self) -> None:
            self._closed = False
            self._stored_findings: Dict[str, List[str]] = {}

        @classmethod
        def from_env(cls) -> "FakeGraphRepo":
            return cls()

        def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            image_payload = payload.get("image") or {}
            image_id = image_payload.get("image_id", "UNKNOWN")
            findings = payload.get("findings") or []
            finding_ids: List[str] = []
            for idx, finding in enumerate(findings):
                fid = finding.get("id") if isinstance(finding, dict) else None
                if not fid:
                    fid = f"MOCK_F_{idx}"
                finding_ids.append(str(fid))
            self._stored_findings[image_id] = list(finding_ids)
            return {"image_id": image_id, "finding_ids": finding_ids}

        def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = deepcopy(payload)
            image = dict(data.get("image") or {})
            image.setdefault("image_id", "FAKE_IMG")
            data["image"] = image
            data.setdefault("case_id", "FAKE_CASE")
            return data

        def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
            return list(self._stored_findings.get(image_id, []))

        def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
            return []

        def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
            return 0

        def close(self) -> None:
            self._closed = True

    class FakeContextBuilder:
        def __init__(self, repo: FakeGraphRepo) -> None:  # pragma: no cover - simple container
            self._repo = repo

        def build_context(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float],
            beta_report: Optional[float],
            k_slots: Optional[Dict[str, int]],
        ) -> GraphContextResult:
            slot_limits = {"findings": 0, "reports": 0, "similarity": 0}
            slot_meta = {
                "requested_k": k,
                "applied_k": k,
                "slot_source": "auto",
                "requested_overrides": dict(k_slots or {}),
                "allocated_total": 0,
            }
            return GraphContextResult(
                summary=[],
                summary_rows=[],
                paths=[],
                facts={"image_id": image_id, "findings": []},
                triples_text="No path generated (0/k)",
                slot_limits=slot_limits,
                slot_meta=slot_meta,
            )

        def close(self) -> None:
            return None

    async def degraded_run_vgl_mode(
        llm: Any,
        image_id: Optional[str],
        context_str: str,
        max_chars: int,
        fallback_to_vl: bool,
        normalized: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assert fallback_to_vl is True
        return {
            "text": "No evidence available for (IMAGE_ID)",
            "latency_ms": 4,
            "degraded": "VL",
        }

    monkeypatch.setattr(pipeline_module, "GraphRepo", FakeGraphRepo)
    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", FakeContextBuilder)
    monkeypatch.setattr(pipeline_module, "run_vgl_mode", degraded_run_vgl_mode)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "image_id": "US001",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 1,
            "max_chars": 60,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    graph_context = payload.get("graph_context", {})
    debug_blob = payload.get("debug", {})
    evaluation = payload.get("evaluation", {})
    assert graph_context.get("fallback_reason") == "no_graph_paths"
    assert graph_context.get("fallback_used") is True
    assert graph_context.get("no_graph_evidence") is True
    assert debug_blob.get("context_fallback_reason") == "no_graph_paths"
    assert debug_blob.get("context_fallback_used") is True
    assert debug_blob.get("context_no_graph_evidence") is True
    assert evaluation.get("context_fallback_reason") == "no_graph_paths"
    assert evaluation.get("context_fallback_used") is True

    results = payload.get("results", {})
    response_image_id = payload.get("image_id")
    consensus = results.get("consensus", {})

    assert results.get("status") == "low_confidence"
    assert consensus.get("status") == "low_confidence"
    notes = str(consensus.get("notes") or "").lower()
    assert "graph context empty" in notes or "fell back to vl" in notes

    vgl_text = results.get("VGL", {}).get("text")
    expected_id = response_image_id or "US001"
    assert vgl_text == f"No evidence available for {expected_id}"


def test_pipeline_prefers_graph_backed_vgl_when_other_modes_diverge(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeGraphRepo:
        def __init__(self) -> None:
            self._closed = False
            self._stored_findings: Dict[str, List[str]] = {}

        @classmethod
        def from_env(cls) -> "FakeGraphRepo":
            return cls()

        def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            image_payload = payload.get("image") or {}
            image_id = image_payload.get("image_id", "US001")
            findings = payload.get("findings") or []
            finding_ids = [
                str(finding.get("id") or f"MOCK_F_{idx}") for idx, finding in enumerate(findings)
            ]
            self._stored_findings[image_id] = list(finding_ids)
            return {"image_id": image_id, "finding_ids": finding_ids}

        def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = deepcopy(payload)
            image = dict(data.get("image") or {})
            image.setdefault("image_id", "FAKE_IMG")
            data["image"] = image
            data.setdefault("case_id", "FAKE_CASE")
            return data

        def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
            return list(self._stored_findings.get(image_id, []))

        def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
            return []

        def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
            return 0

        def close(self) -> None:
            self._closed = True

    class FakeContextBuilder:
        def __init__(self, repo: FakeGraphRepo) -> None:
            self._repo = repo

        def build_context(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float],
            beta_report: Optional[float],
            k_slots: Optional[Dict[str, int]],
        ) -> GraphContextResult:
            summary_lines = ["[EDGE SUMMARY]", "HAS_FINDING: cnt=1, avg_conf=0.9"]
            paths = [
                {
                    "label": "Seed Finding",
                    "triples": ["Image[US001] -HAS_FINDING-> Finding[F201]"],
                    "slot": "findings",
                }
            ]
            facts = {
                "image_id": "US001",
                "findings": [
                    {
                        "id": "F201",
                        "type": "mass",
                        "location": "liver",
                        "conf": 0.88,
                    }
                ],
            }
            slot_meta = {
                "requested_k": k,
                "applied_k": k,
                "slot_source": "auto",
                "requested_overrides": dict(k_slots or {}),
                "allocated_total": 2,
            }
            return GraphContextResult(
                summary=summary_lines,
                summary_rows=[{"rel": "HAS_FINDING", "cnt": 1, "avg_conf": 0.9}],
                paths=paths,
                facts=facts,
                triples_text="Image[US001] -HAS_FINDING-> Finding[F201]",
                slot_limits={"findings": 2, "reports": 0, "similarity": 0},
                slot_meta=slot_meta,
            )

        def close(self) -> None:
            return None

    def mismatched_v_mode(normalized: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        return {"text": "Chest radiograph shows clear lungs", "latency_ms": 5}

    async def mismatched_vl_mode(llm: Any, normalized: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        return {"text": "胸部 X 光片显示无异常阴影。", "latency_ms": 6}

    async def grounded_vgl_mode(
        llm: Any,
        image_id: Optional[str],
        context_str: str,
        max_chars: int,
        fallback_to_vl: bool,
        normalized: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assert fallback_to_vl is True
        return {"text": "Focal hepatic lesion remains stable at 2.1 cm (EVIDENCE).", "latency_ms": 7, "degraded": False}

    monkeypatch.setattr(pipeline_module, "GraphRepo", FakeGraphRepo)
    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", FakeContextBuilder)
    monkeypatch.setattr(pipeline_module, "run_v_mode", mismatched_v_mode)
    monkeypatch.setattr(pipeline_module, "run_vl_mode", mismatched_vl_mode)
    monkeypatch.setattr(pipeline_module, "run_vgl_mode", grounded_vgl_mode)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "image_id": "US001",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["V", "VL", "VGL"],
            "k": 2,
            "max_chars": 80,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    results = payload.get("results", {})
    assert results.get("V", {}).get("degraded") == "graph_mismatch"
    assert results.get("VL", {}).get("degraded") == "graph_mismatch"

    consensus = results.get("consensus", {})
    assert consensus.get("status") == "agree"
    assert consensus.get("agreement_score", 0) >= 0.6
    notes = str(consensus.get("notes") or "")
    assert "graph-grounded mode" in notes

    evaluation = payload.get("evaluation") or {}
    evaluation_status = evaluation.get("status") or (evaluation.get("consensus") or {}).get("status")
    assert evaluation_status == "agree"


def test_pipeline_flags_context_mismatch_when_paths_conflict(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MismatchContextBuilder:
        def __init__(self, repo: Any) -> None:  # pragma: no cover - simple holder
            self._repo = repo

        def build_context(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float],
            beta_report: Optional[float],
            k_slots: Optional[Dict[str, int]],
        ) -> GraphContextResult:
            path = {
                "slot": "findings",
                "label": "Conflicting finding",
                "triples": ["Image[US001] -HAS_FINDING-> Finding[CTX_FX]"],
                "score": 0.9,
            }
            slot_limits = {"findings": 1, "reports": 0, "similarity": 0}
            slot_meta = {
                "requested_k": k,
                "applied_k": k,
                "slot_source": "auto",
                "requested_overrides": dict(k_slots or {}),
                "allocated_total": 1,
            }
            return GraphContextResult(
                summary=["[EDGE SUMMARY]", "No path generated (0/k)"],
                summary_rows=[],
                paths=[path],
                facts={"image_id": image_id, "findings": [{"id": "CTX_FX", "type": "mass"}]},
                triples_text="[EVIDENCE PATHS]\nNo path generated (0/k)",
                slot_limits=slot_limits,
                slot_meta=slot_meta,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", MismatchContextBuilder)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "image_id": "US001",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 1,
            "max_chars": 60,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    debug_blob = payload.get("debug", {})
    errors = payload.get("errors") or []
    assert {"stage": "context", "msg": "facts_paths_mismatch"} in errors
    assert debug_blob.get("context_consistency") is False
    assert debug_blob.get("context_consistency_reason") == "paths_present_but_marked_missing"


def test_pipeline_emits_slot_rebalance_notes(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RebalanceGraphRepo:
        def __init__(self) -> None:
            self._closed = False

        @classmethod
        def from_env(cls) -> "RebalanceGraphRepo":
            return cls()

        def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            return payload

        def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            image = payload.get("image") or {}
            image_id = image.get("image_id", "US_REBAL")
            findings = payload.get("findings") or []
            ids = [str(f.get("id") or f"R_{idx}") for idx, f in enumerate(findings)]
            return {"image_id": image_id, "finding_ids": ids}

        def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
            return list(expected_ids or [])

        def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
            return []

        def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
            return 0

        def close(self) -> None:
            self._closed = True

    class StarvedContextBuilder:
        def __init__(self, repo: Any) -> None:
            self._repo = repo

        def build_context(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float],
            beta_report: Optional[float],
            k_slots: Optional[Dict[str, int]],
        ) -> GraphContextResult:
            slot_limits = {"findings": 0, "reports": 0, "similarity": 0}
            slot_meta = {
                "requested_k": k,
                "applied_k": k,
                "slot_source": "auto",
                "requested_overrides": dict(k_slots or {}),
                "allocated_total": 0,
                "finding_slot_initial": 0,
            }
            path = {
                "slot": "findings",
                "label": "Stable lesion",
                "triples": ["Image[US001] -HAS_FINDING-> Finding[CTX_F1]"],
                "score": 0.9,
            }
            return GraphContextResult(
                summary=["[EDGE SUMMARY]", "HAS_FINDING: cnt=1, avg_conf=0.90"],
                summary_rows=[{"rel": "HAS_FINDING", "cnt": 1, "avg_conf": 0.9}],
                paths=[path],
                facts={"image_id": image_id, "findings": [{"id": "CTX_F1", "type": "mass"}]},
                triples_text="Image[US001] -HAS_FINDING-> Finding[CTX_F1]",
                slot_limits=slot_limits,
                slot_meta=slot_meta,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline_module, "GraphRepo", RebalanceGraphRepo)
    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", StarvedContextBuilder)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "image_id": "US001",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 1,
            "max_chars": 60,
            "parameters": {"k_findings": 0},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    graph_context = payload.get("graph_context", {})
    slot_limits = graph_context.get("slot_limits", {})
    slot_meta = graph_context.get("slot_meta", {})
    debug_blob = payload.get("debug", {})
    evaluation = payload.get("evaluation", {})

    assert slot_limits.get("findings", 0) >= 1
    assert slot_meta.get("retried_findings") is True
    notes = debug_blob.get("context_notes") or []
    assert any("rebalanced" in note for note in notes)
    eval_notes = evaluation.get("notes", "")
    assert "rebalanced" in eval_notes


def test_pipeline_persists_canonical_storage_uri_for_dummy_lookup(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_id = "IMG201"
    canonical_uri = "/data/dummy/IMG201.png"
    lookup_stub = LookupResult(
        image_id=canonical_id,
        storage_uri=canonical_uri,
        modality="US",
        source="alias",
    )

    def _fake_resolve_by_path(cls, path: Optional[str]) -> Optional[LookupResult]:  # type: ignore[override]
        return lookup_stub

    def _fake_resolve_by_id(cls, raw_id: str) -> Optional[LookupResult]:  # type: ignore[override]
        return lookup_stub

    monkeypatch.setattr(
        pipeline_module.DummyImageRegistry,
        "resolve_by_path",
        classmethod(_fake_resolve_by_path),
    )
    monkeypatch.setattr(
        pipeline_module.DummyImageRegistry,
        "resolve_by_id",
        classmethod(_fake_resolve_by_id),
    )

    graph_repo_instances: List["RecordingGraphRepo"] = []

    class RecordingGraphRepo:
        def __init__(self) -> None:
            self.storage_by_id: Dict[str, set[str]] = {}
            self.findings_by_id: Dict[str, List[str]] = {}

        @classmethod
        def from_env(cls) -> "RecordingGraphRepo":  # type: ignore[override]
            instance = cls()
            graph_repo_instances.append(instance)
            return instance

        def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = deepcopy(payload)
            image = dict(data.get("image") or {})
            image.setdefault("image_id", canonical_id)
            data["image"] = image
            data.setdefault("case_id", f"CASE_{canonical_id}")
            return data

        def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            prepared = self.prepare_upsert_parameters(payload)
            image = prepared.get("image") or {}
            image_id = image.get("image_id")
            storage_uri = image.get("storage_uri")
            if image_id:
                bucket = self.storage_by_id.setdefault(str(image_id), set())
                if storage_uri:
                    bucket.add(str(storage_uri))
                findings_payload = prepared.get("findings") or []
                finding_ids = [
                    str((finding or {}).get("id") or f"REC_F_{idx}") for idx, finding in enumerate(findings_payload)
                ]
                self.findings_by_id[str(image_id)] = finding_ids
            else:
                finding_ids = []
            return {"image_id": image_id, "finding_ids": finding_ids}

        def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
            return []

        def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
            return 0

        def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
            return list(self.findings_by_id.get(str(image_id), []))

        def close(self) -> None:
            return None

    class RecordingContextBuilder:
        def __init__(self, repo: RecordingGraphRepo) -> None:
            self._repo = repo

        def build_context(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float] = None,
            beta_report: Optional[float] = None,
            k_slots: Optional[Dict[str, int]] = None,
        ) -> GraphContextResult:
            slot_limits = dict(k_slots or {"findings": k, "reports": 0, "similarity": 0})
            slot_meta = {
                "requested_k": k,
                "applied_k": k,
                "slot_source": "overrides" if k_slots else "auto",
                "requested_overrides": dict(k_slots or {}),
                "allocated_total": sum(slot_limits.values()),
            }
            return GraphContextResult(
                summary=["[EDGE SUMMARY]", "데이터 없음"],
                summary_rows=[],
                paths=[],
                facts={"image_id": image_id, "findings": []},
                triples_text="No path generated (0/k)",
                slot_limits=slot_limits,
                slot_meta=slot_meta,
            )

        def build_bundle(
            self,
            *,
            image_id: str,
            k: int,
            max_chars: int,
            alpha_finding: Optional[float] = None,
            beta_report: Optional[float] = None,
            k_slots: Optional[Dict[str, int]] = None,
        ) -> Dict[str, Any]:
            return self.build_context(
                image_id=image_id,
                k=k,
                max_chars=max_chars,
                alpha_finding=alpha_finding,
                beta_report=beta_report,
                k_slots=k_slots,
            ).to_bundle()

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline_module, "GraphRepo", RecordingGraphRepo)
    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", RecordingContextBuilder)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/dummy-img.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 1,
            "max_chars": 64,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert graph_repo_instances, "GraphRepo.from_env was not invoked"
    repo_instance = graph_repo_instances[-1]
    storage_entries = repo_instance.storage_by_id.get(canonical_id)
    assert storage_entries == {canonical_uri}

    debug_blob = payload.get("debug", {})
    assert debug_blob.get("storage_uri") == canonical_uri


def test_pipeline_auto_context_includes_described_by_path(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG201",
        storage_uri="/data/dummy/IMG201.png",
        modality="US",
        source="alias",
    )
    paths_by_slot = {
        "findings": [
            {
                "label": "Finding F201",
                "triples": ["Image[IMG201] -HAS_FINDING-> Finding[F201]"],
                "score": 0.92,
            }
        ],
        "reports": [
            {
                "label": "Report R201",
                "triples": ["Image[IMG201] -DESCRIBED_BY-> Report[R201]"],
                "score": 0.85,
            }
        ],
    }
    summary_rows = [
        {"rel": "HAS_FINDING", "cnt": 1, "avg_conf": 0.9},
        {"rel": "DESCRIBED_BY", "cnt": 1, "avg_conf": 0.82},
    ]
    facts = {
        "image_id": "IMG201",
        "findings": [
            {"id": "F201", "type": "nodule", "location": "liver", "conf": 0.9},
        ],
    }
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot=paths_by_slot,
        summary_rows=summary_rows,
        facts=facts,
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG201.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["V", "VL", "VGL"],
            "k": 2,
            "max_chars": 80,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    graph_paths = payload.get("graph_context", {}).get("paths", [])
    assert graph_paths, "no context paths returned"
    assert any(
        any("DESCRIBED_BY" in triple for triple in path.get("triples", []))
        for path in graph_paths
    ), f"DESCRIBED_BY path missing: {graph_paths}"

    slot_limits = payload.get("debug", {}).get("context_slot_limits", {})
    assert slot_limits.get("reports", 0) >= 1


def test_pipeline_slot_limits_keep_findings_when_summary_has_findings(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG777",
        storage_uri="/data/dummy/IMG777.png",
        modality="US",
        source="alias",
    )
    paths_by_slot = {
        "reports": [
            {
                "label": "Report node",
                "triples": ["Image[IMG777] -DESCRIBED_BY-> Report[R77]"],
                "score": 0.8,
            }
        ],
        "similarity": [
            {
                "label": "Neighbor IMG770",
                "triples": ["Image[IMG777] -SIMILAR_TO-> Image[IMG770]"],
                "score": 0.7,
            }
        ],
    }
    facts = {
        "image_id": "IMG777",
        "findings": [
            {"id": "F777", "type": "lesion", "location": "liver", "conf": 0.91},
        ],
    }
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot=paths_by_slot,
        summary_rows=[
            {"rel": "DESCRIBED_BY", "cnt": 1, "avg_conf": 0.8},
            {"rel": "SIMILAR_TO", "cnt": 1, "avg_conf": 0.7},
        ],
        facts=facts,
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG777.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 3,
            "max_chars": 80,
        },
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    slot_limits = payload.get("debug", {}).get("context_slot_limits", {})
    assert slot_limits.get("findings", 0) >= 1


def test_pipeline_reports_no_paths_when_graph_returns_none(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG888",
        storage_uri="/data/dummy/IMG888.png",
        modality="CT",
        source="alias",
    )
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot={},  # Simulate graph query returning no explicit paths
        summary_rows=[{"rel": "HAS_FINDING", "cnt": 1, "avg_conf": 0.88}],
        facts={
            "image_id": "IMG888",
            "findings": [
                {"id": "F888", "type": "lesion", "location": "liver", "conf": 0.92},
            ],
        },
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG888.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["V"],
            "k": 2,
            "max_chars": 80,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    debug_blob = payload.get("debug", {})
    assert debug_blob.get("context_paths_len", 0) == 0
    assert debug_blob.get("context_consistency") is True
    graph_paths = payload.get("graph_context", {}).get("paths", [])
    assert graph_paths == []
    triples_block = payload.get("graph_context", {}).get("triples", "")
    assert "No path generated" in triples_block


def test_pipeline_backfills_paths_when_graph_returns_no_facts(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG777X",
        storage_uri="/data/dummy/IMG777X.png",
        modality="CT",
        source="alias",
    )
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot={},
        summary_rows=[],
        facts={"image_id": "IMG777X", "findings": []},
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG777X.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 1,
            "max_chars": 80,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    debug_blob = payload.get("debug", {})
    assert debug_blob.get("context_paths_len", 0) == 0
    graph_context = payload.get("graph_context", {})
    paths = graph_context.get("paths", [])
    assert paths == []
    slot_limits = graph_context.get("slot_limits", {})
    assert isinstance(slot_limits, dict) and slot_limits.get("findings", 0) >= 1
    facts = graph_context.get("facts", {})
    assert isinstance(facts.get("findings"), list) and not facts["findings"], "facts should remain empty when graph has none"


def test_pipeline_raises_error_when_upsert_returns_no_ids(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG999",
        storage_uri="/data/dummy/IMG999.png",
        modality="XR",
        source="alias",
    )
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot={},
        summary_rows=[],
        facts={"image_id": "IMG999", "findings": []},
        force_empty_upsert_ids=True,
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG999.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["V", "VGL"],
            "k": 2,
            "max_chars": 80,
        },
    )
    assert response.status_code == 500
    payload = response.json()
    detail = payload.get("detail") or {}
    assert detail.get("ok") is False
    errors = detail.get("errors") or []
    assert errors, "upsert mismatch error payload missing"
    assert errors[0].get("stage") == "upsert"
    assert errors[0].get("msg") == "finding_upsert_mismatch"


def test_pipeline_provenance_metadata_aligns_across_sections(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG201",
        storage_uri="/data/dummy/IMG201.png",
        modality="US",
        source="alias",
    )
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot={
            "findings": [
                {
                    "label": "Seeded finding",
                    "triples": ["Image[IMG201] -HAS_FINDING-> Finding[SEED_1]"],
                    "score": 0.9,
                }
            ]
        },
        summary_rows=[{"rel": "HAS_FINDING", "cnt": 1, "avg_conf": 0.9}],
        facts={"image_id": "IMG201", "findings": []},
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    response = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={
            "file_path": "/tmp/IMG201.png",
            "image_b64": _SAMPLE_IMAGE_B64,
            "modes": ["VGL"],
            "k": 2,
            "max_chars": 80,
            "parameters": {"force_dummy_fallback": True},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    graph_ctx = payload.get("graph_context", {})
    results = payload.get("results", {})
    evaluation = payload.get("evaluation", {})
    debug_blob = payload.get("debug", {})

    graph_source = graph_ctx.get("finding_source")
    assert graph_source
    assert graph_source == results.get("finding_source") == evaluation.get("finding_source")

    seeded_ids = graph_ctx.get("seeded_finding_ids")
    assert isinstance(seeded_ids, list) and seeded_ids
    assert seeded_ids == results.get("seeded_finding_ids") == evaluation.get("seeded_finding_ids")

    fallback_graph = graph_ctx.get("finding_fallback")
    assert isinstance(fallback_graph, dict) and fallback_graph.get("used") is True
    assert fallback_graph == results.get("finding_fallback")
    assert fallback_graph == evaluation.get("finding_fallback")

    debug_fallback = debug_blob.get("finding_fallback") or {}
    assert debug_fallback.get("used") is True
    assert set(debug_blob.get("seeded_finding_ids") or []) == set(seeded_ids)
    provenance_context = graph_ctx.get("finding_provenance") or {}
    assert provenance_context.get("finding_source") == graph_source
    assert provenance_context == results.get("finding_provenance")
    assert provenance_context == evaluation.get("finding_provenance")


def test_pipeline_report_override_parity_matches_auto(
    pipeline_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = LookupResult(
        image_id="IMG201",
        storage_uri="/data/dummy/IMG201.png",
        modality="US",
        source="alias",
    )
    report_paths = [
        {
            "label": "Report R201",
            "triples": ["Image[IMG201] -DESCRIBED_BY-> Report[R201]"],
            "score": 0.88,
        },
        {
            "label": "Report R202",
            "triples": ["Image[IMG201] -DESCRIBED_BY-> Report[R202]"],
            "score": 0.81,
        },
    ]
    harness = _PipelineHarness(
        lookup=lookup,
        paths_by_slot={"reports": report_paths},
        summary_rows=[{"rel": "DESCRIBED_BY", "cnt": 2, "avg_conf": 0.84}],
        facts={"image_id": "IMG201", "findings": []},
    )
    harness.install(monkeypatch)

    client = TestClient(pipeline_app)
    base_payload = {
        "file_path": "/tmp/IMG201.png",
        "image_b64": _SAMPLE_IMAGE_B64,
        "modes": ["VGL"],
        "k": 2,
        "max_chars": 80,
    }

    resp_auto = client.post("/pipeline/analyze", params={"debug": 1}, json=base_payload)
    assert resp_auto.status_code == 200, resp_auto.text
    paths_auto = resp_auto.json().get("graph_context", {}).get("paths", [])

    resp_override = client.post(
        "/pipeline/analyze",
        params={"debug": 1},
        json={**base_payload, "parameters": {"k_reports": 2}},
    )
    assert resp_override.status_code == 200, resp_override.text
    paths_override = resp_override.json().get("graph_context", {}).get("paths", [])

    assert paths_auto == paths_override
@pytest.fixture(scope="session")
def ensure_dummy_c_seed() -> None:
    if os.getenv("NEO4J_SKIP"):
        pytest.skip("NEO4J_SKIP is set; skipping Neo4j-dependent tests", allow_module_level=True)
    if shutil.which("cypher-shell") is None:
        pytest.skip("cypher-shell command not available", allow_module_level=True)

    try:
        _load_seed_data()
        _upsert_reference_case()
    except subprocess.CalledProcessError:
        pytest.skip("cypher-shell invocation failed; skipping Neo4j-dependent tests", allow_module_level=True)


@pytest.mark.usefixtures("ensure_dummy_c_seed")
def test_query_paths_returns_dense_paths() -> None:
    repo = GraphRepo.from_env()
    try:
        paths = repo.query_paths("US001", k=5)
    finally:
        repo.close()

    assert len(paths) >= 3
    first_path = paths[0]
    assert isinstance(first_path.get("triples"), list)
    assert len(first_path["triples"]) >= 1


@pytest.fixture()
def pipeline_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def fake_normalize_from_vlm(
        file_path: str | None,
        image_id: str | None,
        vlm_runner: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        findings_payload = deepcopy(_FINDINGS_FIXTURE)
        report_ts = datetime.now(timezone.utc).isoformat()
        caption = "Focal hepatic lesion with satellite nodule."
        return {
            "image": {
                "image_id": "US001",
                "path": file_path or "/tmp/us001.png",
                "modality": "US",
            },
            "report": {
                "id": "C_R_1",
                "text": caption,
                "model": "dummy-llm",
                "conf": 0.91,
                "ts": report_ts,
            },
            "findings": findings_payload,
            "caption": caption,
            "vlm_latency_ms": 5,
            "raw_vlm": {"output": caption},
        }

    def fake_run_v_mode(normalized: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        return {"text": "Hepatic lesion remains stable", "latency_ms": 1}

    async def fake_run_vl_mode(llm: Any, normalized: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        return {"text": "Hepatic lesion remains stable", "latency_ms": 2}

    async def fake_run_vgl_mode(
        llm: Any,
        image_id: str | None,
        context_str: str,
        max_chars: int,
        fallback_to_vl: bool,
        normalized: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        return {"text": "Hepatic lesion remains stable", "latency_ms": 3, "degraded": False}

    monkeypatch.setattr(pipeline_module, "normalize_from_vlm", fake_normalize_from_vlm)
    monkeypatch.setattr(pipeline_module, "run_v_mode", fake_run_v_mode)
    monkeypatch.setattr(pipeline_module, "run_vl_mode", fake_run_vl_mode)
    monkeypatch.setattr(pipeline_module, "run_vgl_mode", fake_run_vgl_mode)

    class DummyLLM:
        model = "dummy-llm"

        async def generate(self, prompt: str, temperature: float = 0.2, context: str | None = None) -> Dict[str, Any]:
            return {"output": "Hepatic lesion remains stable", "latency_ms": 2}

    class DummyVLM:
        model = "dummy-vlm"

        async def generate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            return {"output": "ok", "latency_ms": 1}

    lookup_stub = LookupResult(
        image_id="IMG_001",
        storage_uri="/mnt/data/medical_dummy/images/img_001.png",
        modality="US",
        source="alias",
    )

    class FixtureGraphRepo:
        def __init__(self) -> None:
            self._closed = False
            self._stored_findings: Dict[str, List[str]] = {}

        @classmethod
        def from_env(cls) -> "FixtureGraphRepo":
            return cls()

        def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = deepcopy(payload)
            image = dict(data.get("image") or {})
            image.setdefault("image_id", lookup_stub.image_id)
            data["image"] = image
            data.setdefault("case_id", f"CASE_{lookup_stub.image_id}")
            return data

        def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            prepared = self.prepare_upsert_parameters(payload)
            image_id = prepared["image"]["image_id"]
            finding_ids = [str(f.get("id") or f"UP_{idx}") for idx, f in enumerate(prepared.get("findings") or [])]
            self._stored_findings[image_id] = list(finding_ids)
            return {"image_id": image_id, "finding_ids": finding_ids}

        def query_bundle(self, image_id: str) -> Dict[str, Any]:
            return {
                "summary": [
                    {"rel": "HAS_FINDING", "cnt": 2, "avg_conf": 0.9},
                    {"rel": "DESCRIBED_BY", "cnt": 1, "avg_conf": 0.85},
                    {"rel": "SIMILAR_TO", "cnt": 1, "avg_conf": 0.8},
                ],
                "facts": {
                    "image_id": image_id,
                    "findings": [
                        {"id": "CTX_F1", "type": "mass", "location": "liver", "conf": 0.92},
                        {"id": "CTX_F2", "type": "edema", "location": "lung", "conf": 0.81},
                    ],
                },
            }

        def query_paths(
            self,
            image_id: str,
            k: int = 2,
            *,
            alpha_finding: Optional[float] = None,
            beta_report: Optional[float] = None,
            k_slots: Optional[Dict[str, int]] = None,
        ) -> List[Dict[str, Any]]:
            return [
                {
                    "label": "Hepatic lesion",
                    "triples": [
                        f"Image[{image_id}] -HAS_FINDING-> Finding[CTX_F1]",
                        "Finding[CTX_F1] -LOCATED_IN-> Anatomy[Liver]",
                        "Finding[CTX_F1] -RELATED_TO-> Finding[CTX_F2]",
                    ],
                    "score": 0.9,
                    "slot": "findings",
                },
                {
                    "label": "Report context",
                    "triples": [
                        f"Image[{image_id}] -DESCRIBED_BY-> Report[CTX_R1]",
                        "Report[CTX_R1] -MENTIONS-> Finding[CTX_F1]",
                        "Report[CTX_R1] -MENTIONS-> Finding[CTX_F2]",
                    ],
                    "score": 0.83,
                    "slot": "reports",
                },
                {
                    "label": "Similar study",
                    "triples": [
                        f"Image[{image_id}] -SIMILAR_TO-> Image[CTX_SIM_1]",
                        "Image[CTX_SIM_1] -HAS_FINDING-> Finding[SIM_F1]",
                        "Finding[SIM_F1] -LOCATED_IN-> Anatomy[Liver]",
                    ],
                    "score": 0.8,
                    "slot": "similarity",
                },
            ]

        def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
            return []

        def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
            return 0

        def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
            return list(self._stored_findings.get(image_id, []))

        def close(self) -> None:
            self._closed = True

    monkeypatch.setattr(pipeline_module, "GraphRepo", FixtureGraphRepo)
    monkeypatch.setattr(pipeline_module, "GraphContextBuilder", RealGraphContextBuilder)

    def _resolve_by_path(cls, path: Optional[str]) -> LookupResult:  # type: ignore[override]
        return lookup_stub

    def _resolve_by_id(cls, raw_id: str) -> LookupResult:  # type: ignore[override]
        return lookup_stub

    monkeypatch.setattr(pipeline_module.DummyImageRegistry, "resolve_by_path", classmethod(_resolve_by_path))
    monkeypatch.setattr(pipeline_module.DummyImageRegistry, "resolve_by_id", classmethod(_resolve_by_id))

    app = FastAPI()
    app.include_router(pipeline_module.router)

    @app.get("/health/llm")
    async def _health_llm() -> Dict[str, Any]:
        return {"ok": True}

    @app.get("/health/vlm")
    async def _health_vlm() -> Dict[str, Any]:
        return {"ok": True}

    @app.get("/health/neo4j")
    async def _health_neo4j() -> Dict[str, Any]:
        return {"ok": True}

    app.state.llm = DummyLLM()
    app.state.vlm = DummyVLM()

    return app


def test_pipeline_analyze_returns_paths_and_consensus(pipeline_app: FastAPI) -> None:
    client = TestClient(pipeline_app)
    payload = {
        "image_id": "US001",
        "image_b64": _SAMPLE_IMAGE_B64,
        "modes": ["V", "VL", "VGL"],
        "k": 5,
        "max_chars": 60,
    }

    response = client.post("/pipeline/analyze", params={"debug": 1}, json=payload)
    assert response.status_code == 200

    data = response.json()
    debug_blob = data.get("debug", {})
    assert debug_blob.get("context_paths_len", 0) >= 3
    assert debug_blob.get("context_paths_triple_total", 0) >= debug_blob.get("context_paths_len", 0)
    assert isinstance(debug_blob.get("context_slot_limits"), dict)

    graph_context = data.get("graph_context", {})
    paths = graph_context.get("paths", [])
    assert len(paths) >= 3
    assert len(paths[0].get("triples", [])) >= 3
    summary_lines = graph_context.get("summary", [])
    assert any("LOCATED_IN" in line for line in summary_lines)
    if debug_blob.get("similarity_edges_created", 0):
        assert any("SIMILAR_TO" in line for line in summary_lines)
    slot_meta = graph_context.get("slot_meta", {})
    assert isinstance(slot_meta, dict)
    assert "requested_k" in slot_meta
    assert slot_meta.get("slot_source") in {"auto", "overrides"}
    slot_limits = graph_context.get("slot_limits", {})
    assert isinstance(slot_limits, dict)
    assert slot_meta.get("allocated_total") == sum(slot_limits.values())

    consensus = data.get("results", {}).get("consensus", {})
    assert consensus.get("agreement_score", 0) > 0.2

    evaluation = data.get("evaluation")
    assert evaluation is not None
    assert evaluation.get("ctx_paths_len", 0) == debug_blob.get("context_paths_triple_total", 0)
    assert evaluation.get("consensus", {}).get("status") == consensus.get("status")
    assert evaluation.get("agreement_score") == pytest.approx(consensus.get("agreement_score", 0), rel=1e-3)
    assert isinstance(evaluation.get("similar_seed_images"), list)


@pytest.mark.usefixtures("ensure_dummy_c_seed")
def test_upsert_case_idempotent_by_storage_uri() -> None:
    repo = GraphRepo.from_env()
    storage_uri = "/data/test/idempotent/us999.png"
    image_id = "IDEMP_US_999"
    payload = {
        "case_id": "CASE_IDEMP_US_999",
        "image": {
            "image_id": image_id,
            "path": "/tmp/idempotent-us999.png",
            "modality": "US",
            "storage_uri": storage_uri,
        },
        "report": {
            "id": "IDEMP_R_999",
            "text": "Control case for storage_uri idempotency.",
            "model": "dummy-llm",
            "conf": 0.8,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        "findings": deepcopy(_FINDINGS_FIXTURE[:1]),
    }
    try:
        image_ids = []
        for _ in range(5):
            receipt = repo.upsert_case(payload)
            image_ids.append(receipt.get("image_id"))

        assert len(set(image_ids)) == 1, f"expected identical image_id, got {image_ids}"

        counts = repo._run_read(  # type: ignore[attr-defined]
            "MATCH (i:Image {storage_uri:$u}) RETURN count(i) AS cnt", {"u": storage_uri}
        )
        assert counts and counts[0].get("cnt") == 1
    finally:
        repo._run_write(  # type: ignore[attr-defined]
            """
            MATCH (i:Image {image_id:$image_id})
            OPTIONAL MATCH (i)-[r]-()
            DELETE r, i
            """,
            {"image_id": image_id},
        )
        repo.close()


@pytest.mark.usefixtures("ensure_dummy_c_seed")
def test_pipeline_normalises_dummy_id_from_file_path(pipeline_app: FastAPI) -> None:
    client = TestClient(pipeline_app)
    dummy_path = (
        Path(__file__).resolve().parents[1]
        / "grounded-ai"
        / "data"
        / "medical_dummy"
        / "images"
        / "img_001.png"
    )
    if not dummy_path.exists():
        pytest.skip(f"dummy image not available at {dummy_path}")

    cleanup_repo = GraphRepo.from_env()
    try:
        cleanup_repo._run_write(  # type: ignore[attr-defined]
            """
            MATCH (i:Image {image_id:$image_id})
            OPTIONAL MATCH (i)-[r]-()
            DELETE r, i
            """,
            {"image_id": "IMG_001"},
        )
    finally:
        cleanup_repo.close()

    for attempt in range(2):
        response = client.post(
            "/pipeline/analyze",
            params={"debug": 1},
            json={
                "file_path": str(dummy_path),
                "modes": ["V", "VL", "VGL"],
                "k": 2,
                "max_chars": 40,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        debug_blob = payload.get("debug", {})
        assert debug_blob.get("norm_image_id") == "IMG_001"
        assert debug_blob.get("norm_image_id_source") == "dummy_lookup"
        assert debug_blob.get("dummy_lookup_hit") is True
        storage_uri = debug_blob.get("storage_uri")
        assert isinstance(storage_uri, str) and storage_uri.lower().endswith("img_001.png")
