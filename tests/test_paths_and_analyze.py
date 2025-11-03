from __future__ import annotations

import os
import shutil
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.graph_repo import GraphRepo
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


@pytest.fixture(scope="session", autouse=True)
def ensure_dummy_c_seed() -> None:
    if os.getenv("NEO4J_SKIP"):
        pytest.skip("NEO4J_SKIP is set; skipping Neo4j-dependent tests", allow_module_level=True)
    if shutil.which("cypher-shell") is None:
        pytest.skip("cypher-shell command not available", allow_module_level=True)

    _load_seed_data()
    _upsert_reference_case()


def test_query_paths_returns_dense_paths() -> None:
    repo = GraphRepo.from_env()
    try:
        paths = repo.query_paths("US001", k=5)
    finally:
        repo.close()

    assert len(paths) >= 3
    first_path = paths[0]
    assert isinstance(first_path.get("triples"), list)
    assert len(first_path["triples"]) >= 3


@pytest.fixture()
def pipeline_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def fake_normalize_from_vlm(file_path: str | None, image_id: str | None, vlm_runner: Any) -> Dict[str, Any]:
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

    consensus = data.get("results", {}).get("consensus", {})
    assert consensus.get("agreement_score", 0) > 0.2

    evaluation = data.get("evaluation")
    assert evaluation is not None
    assert evaluation.get("ctx_paths_len", 0) == debug_blob.get("context_paths_triple_total", 0)
    assert evaluation.get("consensus", {}).get("status") == consensus.get("status")
    assert evaluation.get("agreement_score") == pytest.approx(consensus.get("agreement_score", 0), rel=1e-3)
    assert isinstance(evaluation.get("similar_seed_images"), list)


def test_upsert_case_idempotent_by_storage_uri() -> None:
    repo = GraphRepo.from_env()
    storage_uri = "/data/test/idempotent/us999.png"
    image_id = "IDEMP_US_999"
    payload = {
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
