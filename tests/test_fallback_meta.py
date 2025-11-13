import pytest

from grounded_ai.api.services.fallback_meta import (
    FallbackMeta,
    FallbackMetaError,
    FallbackMetaGuard,
)


def test_fallback_meta_guard_detects_reassignment():
    meta = FallbackMeta(used=False, forced=False, force=False, strategy=None, registry_hit=False, seeded_ids=[])
    guard = FallbackMetaGuard(meta)
    updated = meta.mark_forced()
    guard.update(updated, stage="forced")
    snapshot = guard.snapshot("snapshot")
    assert snapshot["forced"] is True
    guard.ensure(snapshot, stage="verify")
    broken = dict(snapshot)
    broken["used"] = False
    with pytest.raises(FallbackMetaError):
        guard.ensure(broken, stage="response")

