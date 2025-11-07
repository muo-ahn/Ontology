from __future__ import annotations

from datetime import datetime, timezone
import sys
import types
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from starlette.requests import Request

if "redis" not in sys.modules:
    fake_redis_asyncio = types.ModuleType("redis.asyncio")

    class _FakeRedisClient:
        async def xadd(self, *args, **kwargs):
            return "0-0"

        async def xgroup_create(self, *args, **kwargs):
            return None

        async def xreadgroup(self, *args, **kwargs):
            return []

        async def xack(self, *args, **kwargs):
            return None

        async def aclose(self):
            return None

    def _fake_from_url(*args, **kwargs):
        return _FakeRedisClient()

    fake_redis_asyncio.from_url = _fake_from_url
    fake_redis_asyncio.ResponseError = Exception

    fake_redis = types.ModuleType("redis")
    fake_redis.asyncio = fake_redis_asyncio
    sys.modules["redis"] = fake_redis
    sys.modules["redis.asyncio"] = fake_redis_asyncio

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

from routers import pipeline
from routers.pipeline import AnalyzeReq
from services.dummy_registry import FindingStub


class _DummyLLMRunner:
    model = "dummy-llm"


class _DummyVLMRunner:
    model = "dummy-vlm"


class _FakeGraphRepo:
    def __init__(self) -> None:
        self._closed = False

    def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        finding_ids = [f.get("id") for f in payload.get("findings", []) if f.get("id")]
        return {"image_id": payload["image"]["image_id"], "finding_ids": finding_ids}

    def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
        return []

    def sync_similarity_edges(self, image_id: str, edges_payload: List[Dict[str, Any]]) -> int:
        return 0

    def close(self) -> None:
        self._closed = True


class _FakeContextBuilder:
    def __init__(self, repo: _FakeGraphRepo) -> None:
        self._repo = repo
        self._closed = False

    def build_bundle(
        self,
        *,
        image_id: str,
        k: int,
        max_chars: int,
        alpha_finding: Optional[float],
        beta_report: Optional[float],
        k_slots: Optional[Dict[str, int]],
    ) -> Dict[str, Any]:
        return {
            "paths": [],
            "facts": {"findings": [], "paths": []},
            "summary": {},
            "slot_limits": {},
        }

    def close(self) -> None:
        self._closed = True


@pytest.mark.asyncio
async def test_pipeline_marks_dummy_seed_fallback(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG")

    async def _fake_ensure_dependencies(request) -> None:  # type: ignore[override]
        return None

    async def _fake_normalize_from_vlm(
        file_path: Optional[str],
        image_id: Optional[str],
        vlm_runner: Any,
        *,
        force_dummy_fallback: bool = False,
    ) -> Dict[str, Any]:
        return {
            "image": {"image_id": image_id or "IMG201", "path": file_path, "modality": "XR"},
            "report": {
                "id": "R1",
                "text": "mock caption",
                "model": "dummy",
                "conf": 0.9,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            "findings": [],
            "finding_fallback": {"used": False, "registry_hit": False, "strategy": None, "force": force_dummy_fallback},
        }

    seeded = [
        FindingStub(
            finding_id="F-SEED-1",
            type="nodule",
            location="lung",
            size_cm=1.2,
            conf=0.8,
        )
    ]

    monkeypatch.setattr(pipeline, "_ensure_dependencies", _fake_ensure_dependencies)
    monkeypatch.setattr(pipeline, "normalize_from_vlm", _fake_normalize_from_vlm)
    monkeypatch.setattr(
        pipeline.DummyFindingRegistry,
        "resolve",
        classmethod(lambda cls, _: seeded),
    )
    monkeypatch.setattr(
        pipeline.DummyImageRegistry,
        "resolve_by_path",
        classmethod(lambda cls, path: None),
    )
    monkeypatch.setattr(
        pipeline.DummyImageRegistry,
        "resolve_by_id",
        classmethod(lambda cls, raw_id: None),
    )
    monkeypatch.setattr(
        pipeline.GraphRepo,
        "from_env",
        classmethod(lambda cls: _FakeGraphRepo()),
    )
    monkeypatch.setattr(pipeline, "GraphContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(
        pipeline,
        "compute_similarity_scores",
        lambda **kwargs: ([], []),
    )

    app = FastAPI()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/pipeline/analyze",
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "app": app,
    }
    request = Request(scope)

    payload = AnalyzeReq(
        image_id="IMG201",
        file_path=str(image_path),
        modes=["V"],
        parameters={"force_dummy_fallback": True},
    )

    response = await pipeline.analyze(
        payload=payload,
        request=request,
        sync=True,
        debug=True,
        llm=_DummyLLMRunner(),
        vlm=_DummyVLMRunner(),
    )

    fallback_debug = response.debug.get("finding_fallback") or {}
    assert fallback_debug.get("used") is True
    assert fallback_debug.get("strategy") == "mock_seed"
    assert fallback_debug.get("registry_hit") is True
    assert fallback_debug.get("forced") is True
    assert fallback_debug.get("seeded_ids_head") == ["F-SEED-1"]
