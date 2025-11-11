"""
Image identity resolution service extracted from the /pipeline/analyze router.

Responsible for deriving a stable image identifier, case ID, and storage URI
while encapsulating all DummyImageRegistry lookups and slug logic.
"""

from __future__ import annotations

import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple
from uuid import uuid4

from .dummy_registry import DummyImageRegistry, LookupResult


class ImageIdentityError(Exception):
    """Raised when the image identity service cannot resolve a valid identifier."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ImageIdentityPayload(Protocol):
    """Subset of the pipeline request fields required for identity resolution."""

    case_id: Optional[str]
    image_id: Optional[str]
    file_path: Optional[str]
    idempotency_key: Optional[str]


@dataclass(slots=True)
class ImageIdentity:
    """Resolved identity metadata for downstream graph + debug consumers."""

    image_id: str
    case_id: str
    path: Optional[str]
    storage_uri: Optional[str]
    storage_uri_key: Optional[str]
    image_id_source: str
    lookup_source: Optional[str]
    seed_hit: bool
    lookup_result: Optional[LookupResult]


_INVALID_CHARS = re.compile(r"[^A-Za-z0-9]+")


def identify_image(
    *,
    payload: ImageIdentityPayload,
    normalized_image: Dict[str, Any],
    resolved_path: Optional[str],
    image_path: Optional[str],
) -> Tuple[ImageIdentity, Dict[str, Any]]:
    """
    Resolve the canonical image identity for downstream processing.

    Returns a tuple of (ImageIdentity, normalized_image) where normalized_image is
    the augmented copy that should replace the original blob.
    """

    working_image = dict(normalized_image or {})
    normalized_image_id = working_image.get("image_id")
    lookup_result: Optional[LookupResult] = None
    lookup_source: Optional[str] = None
    image_id_source = "normalizer"

    if payload.image_id:
        candidate = payload.image_id.strip()
        if not candidate:
            raise ImageIdentityError("image_id must not be blank", status_code=422)
        try:
            normalized_image_id = DummyImageRegistry.normalise_id(candidate)
        except ValueError as exc:  # pragma: no cover - registry raises ValueError for invalid ids
            raise ImageIdentityError("image_id must not be blank", status_code=422) from exc
        image_id_source = "payload"
    else:
        derived_image_id, lookup_candidate = _derive_image_id_from_path(
            resolved_path or payload.file_path or image_path or working_image.get("path"),
        )
        if derived_image_id:
            normalized_image_id = derived_image_id
            if lookup_candidate:
                lookup_result = lookup_candidate
                lookup_source = lookup_candidate.source
                image_id_source = "dummy_lookup"
            else:
                image_id_source = "file_path"

    if not normalized_image_id:
        raise ImageIdentityError("unable to derive image identifier", status_code=502)

    try:
        normalized_image_id = DummyImageRegistry.normalise_id(str(normalized_image_id))
    except ValueError as exc:  # pragma: no cover - registry validates the format
        raise ImageIdentityError("unable to derive image identifier", status_code=502) from exc

    if not lookup_result:
        lookup_result = DummyImageRegistry.resolve_by_id(normalized_image_id)
        if lookup_result:
            lookup_source = lookup_result.source
            if image_id_source != "payload":
                image_id_source = "dummy_lookup"

    final_image_path = image_path or payload.file_path or working_image.get("path")
    case_id = payload.case_id or _resolve_case_id(payload, image_path, normalized_image_id)

    lookup_storage_uri = lookup_result.storage_uri if lookup_result else None
    storage_uri = _resolve_seed_storage_uri(resolved_path, normalized_image_id, preferred=lookup_storage_uri)
    if not storage_uri:
        storage_uri = working_image.get("storage_uri")
    if not storage_uri and final_image_path:
        storage_uri = _resolve_seed_storage_uri(final_image_path, normalized_image_id)
        if not storage_uri:
            storage_uri = str(final_image_path)
    if storage_uri and isinstance(storage_uri, str):
        storage_uri = storage_uri.strip() or None

    storage_uri_key = os.path.basename(storage_uri) if storage_uri else None
    if not storage_uri_key and resolved_path:
        storage_uri_key = os.path.basename(str(resolved_path))
    if storage_uri_key:
        storage_uri_key = storage_uri_key.strip() or None

    if lookup_result and lookup_result.modality and not working_image.get("modality"):
        working_image["modality"] = lookup_result.modality

    if final_image_path:
        working_image["path"] = final_image_path
    working_image["image_id"] = normalized_image_id
    if storage_uri:
        working_image["storage_uri"] = storage_uri
    if storage_uri_key:
        working_image["storage_uri_key"] = storage_uri_key

    identity = ImageIdentity(
        image_id=normalized_image_id,
        case_id=case_id,
        path=final_image_path if isinstance(final_image_path, str) else None,
        storage_uri=storage_uri,
        storage_uri_key=storage_uri_key,
        image_id_source=image_id_source,
        lookup_source=lookup_source,
        seed_hit=bool(lookup_result),
        lookup_result=lookup_result,
    )
    return identity, working_image


def _derive_image_id_from_path(path: Optional[str]) -> Tuple[Optional[str], Optional[LookupResult]]:
    if not path:
        return None, None

    lookup = DummyImageRegistry.resolve_by_path(path)
    if lookup:
        return lookup.image_id, lookup

    stem = Path(path).stem
    candidate = _extract_existing_identifier(stem)
    if candidate:
        return candidate, None

    slug_candidate = _build_slug_identifier(stem or path)
    if slug_candidate:
        return slug_candidate, None

    return None, None


def _extract_existing_identifier(stem: Optional[str]) -> Optional[str]:
    if not stem:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", stem).upper()
    if not cleaned:
        return None
    if cleaned.startswith("IMG"):
        return DummyImageRegistry.normalise_id(cleaned)
    return None


def _build_slug_identifier(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    slug = _slugify(value).upper()
    if not slug:
        return None
    slug = slug[:24]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6].upper()
    return f"IMG_{slug}_{digest}"


def _resolve_seed_storage_uri(
    file_path: Optional[str],
    image_id: Optional[str],
    *,
    preferred: Optional[str] = None,
) -> Optional[str]:
    if preferred:
        candidate = preferred.strip()
        if candidate:
            return candidate

    if not file_path:
        return None

    path = Path(file_path)
    raw = str(path)
    if raw.startswith("/mnt/data/medical_dummy/") or raw.startswith("/data/dummy/"):
        return raw

    suffix = path.suffix.lower() or ".png"
    stem = path.stem
    normalized_id = (image_id or "").strip().upper()
    stem_upper = stem.upper()

    if re.match(r"^IMG_\d+$", normalized_id):
        return f"/mnt/data/medical_dummy/images/{normalized_id.lower()}{suffix}"
    if re.match(r"^IMG_\d+$", stem_upper):
        return f"/mnt/data/medical_dummy/images/{stem.lower()}{suffix}"

    if re.match(r"^IMG\d+$", normalized_id):
        return f"/data/dummy/{normalized_id}{suffix}"
    if re.match(r"^IMG\d+$", stem_upper):
        return f"/data/dummy/{stem_upper}{suffix}"

    if re.match(r"^(CT|US|XR)\d+$", normalized_id):
        return f"/data/dummy/{normalized_id}{suffix}"
    if re.match(r"^(CT|US|XR)\d+$", stem_upper):
        return f"/data/dummy/{stem_upper}{suffix}"

    if stem.lower().startswith("img_"):
        return f"/mnt/data/medical_dummy/images/{stem.lower()}{suffix}"

    return raw


def _resolve_case_id(payload: ImageIdentityPayload, image_path: Optional[str], image_id: str) -> str:
    seed = payload.idempotency_key or image_id or (Path(image_path).stem if image_path else None) or uuid4().hex[:12]
    slug = _slugify(str(seed))
    return f"CASE_{slug.upper()}"


def _slugify(value: str) -> str:
    cleaned = _INVALID_CHARS.sub("_", value).strip("_")
    if not cleaned:
        cleaned = uuid4().hex[:12]
    return cleaned[:48]
