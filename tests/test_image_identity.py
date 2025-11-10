from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from services.dummy_registry import DummyImageRegistry, LookupResult
from services.image_identity import ImageIdentityError, identify_image


@dataclass
class PayloadStub:
    case_id: Optional[str] = None
    image_id: Optional[str] = None
    file_path: Optional[str] = None
    idempotency_key: Optional[str] = None


def test_identify_image_prefers_payload_image_id(tmp_path: Path) -> None:
    image_path = tmp_path / "IMG_123.png"
    image_path.write_bytes(b"fake")
    payload = PayloadStub(image_id="img_123", file_path=str(image_path))

    identity, normalized = identify_image(
        payload=payload,
        normalized_image={},
        resolved_path=payload.file_path,
        image_path=None,
    )

    assert identity.image_id == "IMG_123"
    assert identity.case_id == "CASE_IMG_123"
    assert identity.image_id_source == "payload"
    assert normalized["path"] == str(image_path)
    assert normalized["storage_uri"].endswith("img_123.png")


def test_identify_image_uses_registry_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = LookupResult(
        image_id="IMG_999",
        storage_uri="/mnt/data/medical_dummy/images/img_999.png",
        modality="XR",
        source="alias",
    )

    monkeypatch.setattr(DummyImageRegistry, "resolve_by_path", classmethod(lambda cls, path: lookup))
    monkeypatch.setattr(DummyImageRegistry, "resolve_by_id", classmethod(lambda cls, raw_id: lookup))
    monkeypatch.setattr(
        DummyImageRegistry,
        "normalise_id",
        staticmethod(lambda raw_id: str(raw_id).strip().upper()),
    )

    payload = PayloadStub(case_id="CASE_CUSTOM", file_path="/tmp/alias.png")
    identity, normalized = identify_image(
        payload=payload,
        normalized_image={"modality": None},
        resolved_path=payload.file_path,
        image_path=None,
    )

    assert identity.image_id == "IMG_999"
    assert identity.case_id == "CASE_CUSTOM"
    assert identity.seed_hit is True
    assert identity.lookup_source == "alias"
    assert identity.storage_uri == lookup.storage_uri
    assert normalized["modality"] == "XR"


def test_identify_image_raises_when_identifier_missing() -> None:
    payload = PayloadStub()
    with pytest.raises(ImageIdentityError) as excinfo:
        identify_image(
            payload=payload,
            normalized_image={},
            resolved_path=None,
            image_path=None,
        )
    assert excinfo.value.status_code == 502
