from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.normalizer import (
    _fallback_findings_from_caption,
    _normalise_findings,
    normalize_from_vlm,
)


class DummyVLMRunner:
    class _TaskEnum:
        CAPTION = "caption"

    Task = _TaskEnum()

    def __init__(self, payload: dict | None = None):
        self._payload = payload or {}
        self.model = self._payload.get("model", "dummy-vlm")

    async def generate(self, image_bytes: bytes, prompt: str, task: str) -> dict:
        assert task == self.Task.CAPTION
        return dict(self._payload)


def test_fallback_uses_dummy_registry() -> None:
    fallbacks, registry_hit = _fallback_findings_from_caption("", "IMG201")
    assert registry_hit is True
    assert [f["id"] for f in fallbacks] == ["F201", "F202"]
    assert all(entry.get("source") == "mock_seed" for entry in fallbacks)

    normalised = _normalise_findings(fallbacks, "IMG201")
    assert [entry["id"] for entry in normalised] == ["F201", "F202"]
    assert all(entry.get("source") == "mock_seed" for entry in normalised)


def test_fallback_keyword_path_when_registry_miss() -> None:
    caption = "Nodule in the RML measuring 1.3 cm"
    fallbacks, registry_hit = _fallback_findings_from_caption(caption, "IMG999")
    assert registry_hit is False
    assert len(fallbacks) == 1
    entry = fallbacks[0]
    assert entry["type"] == "nodule"
    assert entry["location"] == "right middle lobe"
    assert entry["size_cm"] == 1.3
    assert entry["source"] == "caption_keywords"

    normalised = _normalise_findings(fallbacks, "IMG999")
    assert len(normalised) == 1
    norm_entry = normalised[0]
    assert norm_entry["type"] == "nodule"
    assert norm_entry["location"] == "right middle lobe"
    assert norm_entry["size_cm"] == 1.3
    assert norm_entry["source"] == "caption_keywords"
    assert norm_entry["id"].startswith("f_")


@pytest.mark.asyncio
async def test_normalize_from_vlm_populates_mock_seed_fallback(tmp_path: Path) -> None:
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG")
    runner = DummyVLMRunner({"output": "", "latency_ms": 5})

    normalized = await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG201",
        vlm_runner=runner,
    )

    fallback = normalized.get("finding_fallback")
    assert fallback == {
        "used": True,
        "registry_hit": True,
        "strategy": "mock_seed",
        "force": False,
    }
    findings = normalized.get("findings") or []
    assert [item.get("id") for item in findings] == ["F201", "F202"]
    assert all(entry.get("source") == "mock_seed" for entry in findings)


@pytest.mark.asyncio
async def test_normalize_from_vlm_keyword_fallback(tmp_path: Path) -> None:
    image_path = tmp_path / "img2.png"
    image_path.write_bytes(b"\x89PNG")
    runner = DummyVLMRunner({"output": "Right middle lobe nodule measuring 1.5 cm"})

    normalized = await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG999",
        vlm_runner=runner,
    )

    fallback = normalized.get("finding_fallback")
    assert fallback == {
        "used": True,
        "registry_hit": False,
        "strategy": "caption_keywords",
        "force": False,
    }
    findings = normalized.get("findings") or []
    assert len(findings) == 1
    entry = findings[0]
    assert entry["type"] == "nodule"
    assert entry["location"] == "right middle lobe"
    assert entry["size_cm"] == 1.5
    assert entry["source"] == "caption_keywords"


@pytest.mark.asyncio
async def test_normalize_from_vlm_forced_fallback(tmp_path: Path) -> None:
    image_path = tmp_path / "img3.png"
    image_path.write_bytes(b"\x89PNG")
    payload = {
        "output": json.dumps(
            {
                "findings": [
                    {"id": "raw-1", "type": "mass", "location": "lung", "size_cm": 1.2, "conf": 0.9}
                ]
            }
        )
    }
    runner = DummyVLMRunner(payload)

    normalized = await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG201",
        vlm_runner=runner,
        force_dummy_fallback=True,
    )

    fallback = normalized.get("finding_fallback")
    assert fallback == {
        "used": True,
        "registry_hit": True,
        "strategy": "mock_seed",
        "force": True,
    }
    findings = normalized.get("findings") or []
    assert [item.get("id") for item in findings] == ["F201", "F202"]


@pytest.mark.asyncio
async def test_normalize_from_vlm_cache_seed_reuses_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("VISION_DEBUG_CACHE_DIR", str(cache_dir))
    image_path = tmp_path / "cache_img.png"
    image_path.write_bytes(b"\x89PNG")

    class RecordingRunner(DummyVLMRunner):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def generate(self, image_bytes: bytes, prompt: str, task: str) -> dict:  # type: ignore[override]
            assert task == self.Task.CAPTION
            self.calls += 1
            return {
                "output": json.dumps(
                    {
                        "image": {"image_id": "IMG-CACHE", "modality": "US"},
                        "report": {"text": f"call-{self.calls}", "model": "dummy-vlm"},
                        "findings": [
                            {"id": f"raw-{self.calls}", "type": "lesion", "location": "liver", "conf": 0.9}
                        ],
                    }
                ),
                "latency_ms": 5,
                "model": "dummy-vlm",
            }

    runner = RecordingRunner()
    first = await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG-CACHE",
        vlm_runner=runner,
        cache_seed="IMG-CACHE",
        enable_cache=True,
    )
    second = await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG-CACHE",
        vlm_runner=runner,
        cache_seed="IMG-CACHE",
        enable_cache=True,
    )

    assert runner.calls == 1, "cached call should not invoke the VLM twice"
    assert second.get("raw_vlm") == {"cached": True}
    assert first["findings"] == second["findings"]
    cache_files = list(cache_dir.glob("normalized_*.json"))
    assert cache_files, "cache file was not materialized on disk"


@pytest.mark.asyncio
async def test_normalize_from_vlm_cache_key_includes_force_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache_force"
    monkeypatch.setenv("VISION_DEBUG_CACHE_DIR", str(cache_dir))
    image_path = tmp_path / "force_img.png"
    image_path.write_bytes(b"\x89PNG")

    class CountingRunner(DummyVLMRunner):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def generate(self, image_bytes: bytes, prompt: str, task: str) -> dict:  # type: ignore[override]
            self.calls += 1
            return {
                "output": json.dumps(
                    {
                        "image": {"image_id": "IMG-FORCE", "modality": "CT"},
                        "report": {"text": "force check", "model": "dummy-vlm"},
                        "findings": [{"id": f"raw-{self.calls}", "type": "lesion", "location": "liver", "conf": 0.9}],
                    }
                ),
                "latency_ms": 3,
                "model": "dummy-vlm",
            }

    runner = CountingRunner()
    await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG-FORCE",
        vlm_runner=runner,
        cache_seed="IMG-FORCE",
        enable_cache=True,
        force_dummy_fallback=False,
    )
    await normalize_from_vlm(
        file_path=str(image_path),
        image_id="IMG-FORCE",
        vlm_runner=runner,
        cache_seed="IMG-FORCE",
        enable_cache=True,
        force_dummy_fallback=True,
    )

    assert runner.calls == 2, "force flag should produce independent cache entries"
