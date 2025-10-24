"""Service health probes aggregated under /health."""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


def _app_version() -> str:
    return (
        os.getenv("APP_VERSION")
        or os.getenv("GIT_SHA")
        or os.getenv("COMMIT_SHA")
        or "dev"
    )


async def _llm_ok(request: Request) -> bool:
    runner = getattr(request.app.state, "llm", None)
    if runner is None:
        return False
    try:
        return bool(await runner.health())
    except Exception:
        return False


async def _vlm_ok(request: Request) -> bool:
    runner = getattr(request.app.state, "vlm", None)
    if runner is None:
        return False
    try:
        return bool(await runner.health())
    except Exception:
        return False


async def _neo4j_ok(request: Request) -> bool:
    client = getattr(request.app.state, "neo4j", None)
    if client is None:
        return False
    health_method = getattr(client, "health", None)
    if callable(health_method):
        try:
            return bool(await health_method())
        except Exception:
            return False
    try:
        rows = await client.run_query("RETURN 1 AS up")  # type: ignore[attr-defined]
    except Exception:
        return False
    return bool(rows and rows[0].get("up") == 1)


async def _collect_status(request: Request) -> Dict[str, bool]:
    return {
        "llm": await _llm_ok(request),
        "vlm": await _vlm_ok(request),
        "neo4j": await _neo4j_ok(request),
    }


@router.get("/health", name="health_root")
async def health_root(request: Request) -> Dict[str, Any]:
    statuses = await _collect_status(request)
    return {
        "ok": all(statuses.values()),
        "services": list(statuses.keys()),
        "version": _app_version(),
        "details": statuses,
    }


@router.get("/health/llm", name="health_llm")
async def health_llm(request: Request) -> Dict[str, bool]:
    return {"ok": await _llm_ok(request)}


@router.get("/health/vlm", name="health_vlm")
async def health_vlm(request: Request) -> Dict[str, bool]:
    return {"ok": await _vlm_ok(request)}


@router.get("/health/neo4j", name="health_neo4j")
async def health_neo4j(request: Request) -> Dict[str, bool]:
    return {"ok": await _neo4j_ok(request)}


__all__ = ["router"]
