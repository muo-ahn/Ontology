"""Lookup helpers that align dummy dataset inputs with seeded Neo4j image nodes."""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DATA_ROOT = Path(
    os.getenv(
        "MEDICAL_DUMMY_DIR",
        Path(__file__).resolve().parents[2] / "data" / "medical_dummy",
    )
)
_IMAGING_FILE = _DATA_ROOT / "imaging.csv"
_ALIASES_FILE = _DATA_ROOT / "imaging_aliases.csv"


@dataclass(frozen=True)
class LookupResult:
    image_id: str
    storage_uri: Optional[str]
    modality: Optional[str]
    source: str


def _canonical_filename(name: str) -> str:
    """Normalise file names for case-insensitive lookup."""

    canonical = name.strip().lower()
    # Collapse whitespace and repeated separators
    canonical = re.sub(r"[\s]+", "-", canonical)
    # Ensure forward slashes are removed prior to matching
    canonical = canonical.replace("/", "-").replace("\\", "-")
    return canonical


def _derive_candidate_from_name(name: str) -> Optional[str]:
    """Extract IMG### style identifiers embedded in filenames."""

    match = re.search(r"(img)[_\-]?(\d{3})", name)
    if match:
        return f"{match.group(1).upper()}_{match.group(2)}"
    return None


@lru_cache()
def _load_imaging_rows() -> Dict[str, Dict[str, Optional[str]]]:
    """Return imaging rows keyed by canonical image_id."""

    rows: Dict[str, Dict[str, Optional[str]]] = {}
    if not _IMAGING_FILE.exists():
        logger.warning("dummy_registry.imaging_file_missing", extra={"path": str(_IMAGING_FILE)})
        return rows

    with _IMAGING_FILE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            image_id = raw.get("id")
            if not image_id:
                continue
            canonical_id = DummyImageRegistry.normalise_id(image_id)
            rows[canonical_id] = {
                "storage_uri": raw.get("file_path"),
                "modality": raw.get("modality"),
            }
    return rows


@lru_cache()
def _load_alias_map() -> Dict[str, str]:
    """Return filename aliases keyed by canonical filename."""

    alias_map: Dict[str, str] = {}
    rows = _load_imaging_rows()
    for image_id, record in rows.items():
        storage_uri = record.get("storage_uri")
        if not storage_uri:
            continue
        canonical_alias = _canonical_filename(Path(storage_uri).name)
        alias_map.setdefault(canonical_alias, image_id)

    if not _ALIASES_FILE.exists():
        return alias_map

    with _ALIASES_FILE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            alias = raw.get("alias")
            image_id = raw.get("image_id")
            if not alias or not image_id:
                continue
            canonical_alias = _canonical_filename(alias)
            alias_map[canonical_alias] = DummyImageRegistry.normalise_id(image_id)
    return alias_map


class DummyImageRegistry:
    """Facade for resolving dummy dataset identifiers."""

    @staticmethod
    def normalise_id(raw_id: str) -> str:
        if raw_id is None:
            raise ValueError("image_id cannot be None")
        cleaned = raw_id.strip()
        if not cleaned:
            raise ValueError("image_id cannot be blank")
        cleaned = cleaned.replace("-", "_")
        cleaned = cleaned.replace(" ", "")
        cleaned = re.sub(r"_+", "_", cleaned.upper())
        return cleaned

    @classmethod
    def resolve_by_id(cls, raw_id: str) -> Optional[LookupResult]:
        canonical_id = cls.normalise_id(raw_id)
        rows = _load_imaging_rows()
        record = rows.get(canonical_id)
        if not record:
            return None
        return LookupResult(
            image_id=canonical_id,
            storage_uri=(record.get("storage_uri") or None),
            modality=(record.get("modality") or None),
            source="id",
        )

    @classmethod
    def resolve_by_path(cls, path: Optional[str]) -> Optional[LookupResult]:
        if not path:
            return None

        name = Path(path).name
        if not name:
            return None
        canonical_name = _canonical_filename(name)

        alias_map = _load_alias_map()
        rows = _load_imaging_rows()

        candidate_id = alias_map.get(canonical_name)
        source = "alias" if candidate_id else None

        if not candidate_id:
            candidate_id = _derive_candidate_from_name(canonical_name)
            source = "filename" if candidate_id else None

        if candidate_id:
            candidate_id = cls.normalise_id(candidate_id)
            record = rows.get(candidate_id)
            if record:
                return LookupResult(
                    image_id=candidate_id,
                    storage_uri=(record.get("storage_uri") or None),
                    modality=(record.get("modality") or None),
                    source=source or "filename",
                )
        return None


__all__ = [
    "DummyImageRegistry",
    "LookupResult",
]
