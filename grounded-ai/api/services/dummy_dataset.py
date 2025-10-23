"""Utility helpers for working with the bundled medical dummy dataset."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

try:  # pragma: no cover - runtime import guard for scripts without dependencies
    from models.pipeline import FindingModel
except Exception:  # pragma: no cover
    from dataclasses import dataclass

    @dataclass
    class FindingModel:  # type: ignore[override]
        id: str
        type: str
        location: Optional[str] = None
        size_cm: Optional[float] = None
        conf: Optional[float] = None


_DATA_ROOT = Path(
    os.getenv(
        "MEDICAL_DUMMY_DIR",
        Path(__file__).resolve().parents[2] / "data" / "medical_dummy",
    )
)
_GROUND_TRUTH_FILE = _DATA_ROOT / "ground_truth.json"


@lru_cache()
def load_ground_truth() -> dict[str, dict[str, Any]]:
    """Load the canned metadata for the three sample images."""

    if not _GROUND_TRUTH_FILE.exists():
        return {}
    data = json.loads(_GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    return {entry["image_id"].upper(): entry for entry in data}


def normalise_image_id(image_id: str) -> str:
    """Normalise image identifiers to the `IMG_###` form used by the dataset."""

    cleaned = image_id.strip().replace("-", "_").upper()
    if cleaned.startswith("IMG") and "_" not in cleaned:
        cleaned = cleaned[:3] + "_" + cleaned[3:]
    return cleaned


def lookup_entry(*, image_id: Optional[str] = None, file_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return ground-truth metadata when we can match the image id or file name."""

    records = load_ground_truth()
    if image_id:
        candidate = records.get(normalise_image_id(image_id))
        if candidate:
            return candidate
    if file_path:
        name = Path(file_path).name.lower()
        for entry in records.values():
            if entry.get("file_name", "").lower() == name:
                return entry
    return None


def decode_image_payload(image_b64: Optional[str], file_path: Optional[str]) -> tuple[bytes, Optional[str]]:
    """Decode an image payload from either base64 input or a file path."""

    if image_b64:
        return base64.b64decode(image_b64), None
    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Image path not found: {file_path}")
        return path.read_bytes(), str(path)
    raise ValueError("Either image_b64 or file_path must be provided")


def ensure_image_id(
    *,
    entry: Optional[dict[str, Any]],
    explicit_id: Optional[str],
    image_bytes: bytes,
) -> str:
    """Derive a stable image identifier."""

    if explicit_id:
        return normalise_image_id(explicit_id)
    if entry:
        return normalise_image_id(entry["image_id"])
    digest = hashlib.sha1(image_bytes).hexdigest()[:8]
    return f"IMG_{digest.upper()}"


def default_caption(entry: Optional[dict[str, Any]], vlm_output: str) -> str:
    """Prefer the curated caption when the model is mocked or empty."""

    text = vlm_output.strip()
    if entry and (not text or text.startswith("[mock-")):
        return entry["caption"]
    return text or (entry["caption"] if entry else "")


def build_findings(image_id: str, caption: str, entry: Optional[dict[str, Any]]) -> list[FindingModel]:
    """Return structured findings using curated metadata or lightweight heuristics."""

    if entry and entry.get("findings"):
        return [FindingModel(**finding) for finding in entry["findings"]]

    lower = caption.lower()
    findings: list[FindingModel] = []
    if "nodule" in lower:
        size_match = re.search(r"(~|≈)?\s*(\d+(?:\.\d+)?)\s*cm", caption)
        size_cm = float(size_match.group(2)) if size_match else None
        findings.append(
            FindingModel(
                id=f"F_{image_id}_N", type="nodule", location="RUL" if "upper" in lower else None, size_cm=size_cm, conf=0.6
            )
        )
    if "fatty" in lower and "liver" in lower:
        findings.append(FindingModel(id=f"F_{image_id}_L", type="fatty_liver", location="liver", conf=0.6))
    if "tachycardia" in lower:
        findings.append(FindingModel(id=f"F_{image_id}_T", type="tachycardia", location="heart", conf=0.55))
    if not findings:
        fallback_conf = default_confidence(entry)
        findings.append(
            FindingModel(
                id=f"F_{image_id}_OBS",
                type="observation",
                location=None,
                size_cm=None,
                conf=fallback_conf,
            )
        )
    return findings


def default_confidence(entry: Optional[dict[str, Any]]) -> float:
    """Confidence fallback when the vLM cannot provide one."""

    if entry and entry.get("vlm_confidence") is not None:
        return float(entry["vlm_confidence"])
    return 0.5


def ensure_case_id(entry: Optional[dict[str, Any]], requested_case_id: Optional[str]) -> Optional[str]:
    """Choose a case identifier in priority order: explicit → curated → generated."""

    if requested_case_id:
        return requested_case_id
    if entry and entry.get("case_id"):
        return entry["case_id"]
    return None


def build_report(
    *,
    image_id: str,
    caption: str,
    model: str,
    entry: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Create a lightweight report document for Neo4j upserts."""

    report_id = entry.get("report_id") if entry else None
    if not report_id:
        report_id = f"R_{image_id}_{uuid4().hex[:8]}"
    confidence = float(entry.get("vlm_confidence", 0.75)) if entry else 0.5
    return {
        "id": report_id,
        "text": caption,
        "model": model,
        "conf": confidence,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def default_summary(entry: Optional[dict[str, Any]]) -> Optional[str]:
    """Return the curated Korean one-line summary when available."""

    if entry:
        return entry.get("llm_summary")
    return None


def expected_keywords(entry: Optional[dict[str, Any]]) -> Iterable[str]:
    """Expose factual keywords for downstream evaluation scripts."""

    if not entry:
        return []
    return entry.get("keywords", [])


def blacklist_terms(entry: Optional[dict[str, Any]]) -> Iterable[str]:
    """Expose hallucination guardrail keywords for evaluation scripts."""

    if not entry:
        return []
    return entry.get("blacklist", [])
