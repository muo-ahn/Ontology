from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

if "py2neo" not in sys.modules:
    py2neo_stub = types.ModuleType("py2neo")
    py2neo_stub.Graph = object  # type: ignore[attr-defined]
    py2neo_stub.Node = object  # type: ignore[attr-defined]
    py2neo_stub.Relationship = object  # type: ignore[attr-defined]
    sys.modules["py2neo"] = py2neo_stub
    errors_stub = types.ModuleType("py2neo.errors")
    errors_stub.ClientError = Exception  # type: ignore[attr-defined]
    sys.modules["py2neo.errors"] = errors_stub

from services.dummy_registry import (
    DummyFindingRegistry,
    DummyImageRegistry,
    FindingStub,
    LookupResult,
)


def test_normalise_id_adds_separator() -> None:
    assert DummyImageRegistry.normalise_id("img123") == "IMG123"
    assert DummyImageRegistry.normalise_id("IMG_123") == "IMG_123"


def test_normalise_id_rejects_blank() -> None:
    with pytest.raises(ValueError):
        DummyImageRegistry.normalise_id("   ")


def test_resolve_by_id_returns_seeded_storage_uri() -> None:
    result = DummyImageRegistry.resolve_by_id("img_001")
    assert isinstance(result, LookupResult)
    assert result.image_id == "IMG_001"
    assert result.storage_uri is not None and result.storage_uri.endswith("img_001.png")
    assert result.source == "id"


def test_resolve_by_path_matches_alias_filename() -> None:
    alias_path = Path("/tmp/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png")
    result = DummyImageRegistry.resolve_by_path(str(alias_path))
    assert isinstance(result, LookupResult)
    assert result.image_id == "IMG201"
    assert result.source in {"alias", "dummy_lookup", "filename", "id"}


def test_resolve_by_path_handles_direct_filename() -> None:
    direct_path = Path("/mnt/data/medical_dummy/images/img_003.png")
    result = DummyImageRegistry.resolve_by_path(str(direct_path))
    assert isinstance(result, LookupResult)
    assert result.image_id == "IMG_003"


def test_resolve_by_path_unknown_returns_none() -> None:
    result = DummyImageRegistry.resolve_by_path("/tmp/does-not-exist.png")
    assert result is None


def test_dummy_finding_registry_returns_seeded_set() -> None:
    findings = DummyFindingRegistry.resolve("img201")
    assert isinstance(findings, list)
    assert [stub.finding_id for stub in findings] == ["F201", "F202"]
    assert all(isinstance(stub, FindingStub) for stub in findings)


def test_dummy_finding_registry_handles_unknown() -> None:
    assert DummyFindingRegistry.resolve("unknown") == []
