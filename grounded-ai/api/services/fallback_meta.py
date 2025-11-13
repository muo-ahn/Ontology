"""Typed helpers for managing fallback metadata across the pipeline."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["FallbackMeta", "FallbackMetaError", "coerce_fallback_meta", "FallbackMetaGuard"]


class FallbackMetaError(ValueError):
    """Raised when fallback metadata is reassigned or mutated unexpectedly."""


class FallbackMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    used: bool
    forced: bool
    force: bool | None = None
    strategy: Optional[str] = None
    registry_hit: bool = False
    seeded_ids: list[str] = Field(default_factory=list)

    def mark_forced(self) -> "FallbackMeta":
        return self.model_copy(update={"forced": True, "used": True, "force": True})

    def with_seeded_ids(self, seeded_ids: list[str]) -> "FallbackMeta":
        return self.model_copy(update={"seeded_ids": seeded_ids})

    def mark_used(self, *, strategy: Optional[str] = None, registry_hit: Optional[bool] = None) -> "FallbackMeta":
        payload: Dict[str, Any] = {"used": True}
        if strategy is not None:
            payload["strategy"] = strategy
        if registry_hit is not None:
            payload["registry_hit"] = registry_hit
        return self.model_copy(update=payload)


def coerce_fallback_meta(payload: Dict[str, Any] | FallbackMeta | None) -> FallbackMeta:
    if isinstance(payload, FallbackMeta):
        return payload
    data = dict(payload or {})
    used = bool(data.get("used"))
    forced = bool(data.get("forced")) or bool(data.get("force"))
    force_flag = data.get("force") if isinstance(data.get("force"), bool) else forced
    return FallbackMeta(
        used=used,
        forced=forced,
        force=force_flag,
        strategy=data.get("strategy"),
        registry_hit=bool(data.get("registry_hit")),
        seeded_ids=list(data.get("seeded_ids") or []),
    )


class FallbackMetaGuard:
    """Tracks fallback metadata across stages and ensures it isn't reassigned."""

    def __init__(self, meta: FallbackMeta, *, stage: str = "init") -> None:
        self._meta = meta
        self._history: list[Dict[str, Any]] = []
        self._record(stage)

    def _record(self, stage: str) -> None:
        snapshot = {"stage": stage, **self._meta.model_dump()}
        self._history.append(snapshot)

    def update(self, meta: FallbackMeta, *, stage: str) -> None:
        self._meta = meta
        self._record(stage)

    def snapshot(self, stage: str) -> Dict[str, Any]:
        payload = self._meta.model_dump()
        self._history.append({"stage": stage, **payload})
        return dict(payload)

    def ensure(self, payload: Dict[str, Any], *, stage: str) -> None:
        expected = self._meta.model_dump()
        mismatches = []
        for key in ("used", "forced", "force", "strategy", "registry_hit"):
            if expected.get(key) != payload.get(key):
                mismatches.append(key)
        expected_ids = expected.get("seeded_ids") or []
        payload_ids = list(payload.get("seeded_ids") or [])
        if expected_ids != payload_ids:
            mismatches.append("seeded_ids")
        if mismatches:
            raise FallbackMetaError(f"fallback meta mismatch at stage={stage}: {mismatches}")

    @property
    def history(self) -> list[Dict[str, Any]]:
        return list(self._history)
