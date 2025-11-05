"""Shared helpers for normalising VLM caption outputs across endpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.dummy_registry import DummyFindingRegistry, FindingStub
from services.vlm_runner import VLMRunner


_KEYWORD_MAP: Dict[str, str] = {
    "nodule": "nodule",
    "결절": "nodule",
    "opacity": "opacity",
    "음영": "opacity",
}

_LOBE_MAP: Dict[str, str] = {
    "rul": "right upper lobe",
    "rml": "right middle lobe",
    "rll": "right lower lobe",
    "lul": "left upper lobe",
    "lll": "left lower lobe",
}


def _force_json_prompt() -> str:
    """Return a robust instruction that forces JSON responses."""

    return (
        "You are a radiology assistant."
        " Respond ONLY with JSON using this schema: {"
        '"image":{"modality":"XR|CT|MR", "image_id":"string?"},'
        '"report":{"id":"string?","text":"string","model":"string?","conf":0-1,"ts":"iso?"},'
        '"findings":[{"id":"string?","type":"string","location":"string?","size_cm":number?,'
        '"conf":0-1?}],"caption":"string","caption_ko":"string?"}. '
        "Ensure valid JSON with double quotes."
    )


def _derive_image_id(file_path: str) -> str:
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"IMG_{digest}"


def _derive_report_id(image_id: str, text: str, model: Optional[str]) -> str:
    key_text = (text or "")[:256]
    seed = f"{image_id}|{key_text}|{model or ''}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"R_{digest}"


def _derive_finding_id(
    image_id: str,
    finding_type: Optional[str],
    location: Optional[str],
    size_cm: Optional[float],
) -> str:
    size_component = "na" if size_cm is None else f"{round(float(size_cm), 1):.1f}"
    seed = "|".join([
        image_id.strip().lower(),
        (finding_type or "").strip().lower(),
        (location or "").strip().lower(),
        size_component,
    ])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"f_{digest}"


def clamp_one_line(text: str, max_chars: int = 120) -> str:
    cleaned = " ".join((text or "").split())
    if max_chars <= 0:
        return cleaned
    return cleaned[:max_chars]


def _fallback_findings_from_caption(
    caption: str,
    image_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Generate fallback findings and report whether dummy registry matched."""

    registry_hit = False

    if image_id:
        try:
            seeded: List[FindingStub] = DummyFindingRegistry.resolve(image_id)
        except ValueError:
            seeded = []
        if seeded:
            registry_hit = True
            return (
                [
                    {
                        "id": stub.finding_id,
                        "type": stub.type,
                        "location": stub.location,
                        "size_cm": stub.size_cm,
                        "conf": stub.conf,
                        "source": stub.source,
                    }
                    for stub in seeded
                ],
                registry_hit,
            )

    text = (caption or "").strip()
    if not text:
        return [], registry_hit

    lowered = text.lower()
    finding_type: Optional[str] = None
    for keyword, normalised in _KEYWORD_MAP.items():
        if keyword in lowered or keyword in text:
            finding_type = normalised
            break

    if not finding_type:
        return [], registry_hit

    location: Optional[str] = None
    for code, label in _LOBE_MAP.items():
        pattern = re.compile(rf"\b{re.escape(code)}\b", re.IGNORECASE)
        if pattern.search(text) or label.lower() in lowered:
            location = label
            break

    size_cm: Optional[float] = None
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*cm", text, re.IGNORECASE)
    if size_match:
        try:
            size_cm = round(float(size_match.group(1)), 1)
        except ValueError:
            size_cm = None

    return (
        [
            {
                "id": None,
                "type": finding_type,
                "location": location,
                "size_cm": size_cm,
                "conf": 0.5,
                "source": "caption_keywords",
            }
        ],
        registry_hit,
    )


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clamp_conf(value: Any) -> Optional[float]:
    conf = _coerce_float(value)
    if conf is None:
        return None
    return max(0.0, min(1.0, conf))


def _parse_json_output(output: str) -> Dict[str, Any]:
    text = (output or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {}
    return {}


def _normalise_findings(
    raw_findings: Iterable[Any],
    image_id: str,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        finding_type = item.get("type")
        location = item.get("location")
        size_cm = _coerce_float(item.get("size_cm"))
        if size_cm is not None:
            size_cm = round(size_cm, 1)
        conf = _clamp_conf(item.get("conf"))
        finding_id = item.get("id")
        if not finding_id:
            finding_id = _derive_finding_id(image_id, finding_type, location, size_cm)
        source = item.get("source")
        findings.append(
            {
                "id": finding_id,
                "type": finding_type,
                "location": location,
                "size_cm": size_cm,
                "conf": conf,
                **({"source": source} if source else {}),
            }
        )
    return findings


async def normalize_from_vlm(
    file_path: Optional[str],
    image_id: Optional[str],
    vlm_runner: VLMRunner,
    *,
    force_dummy_fallback: bool = False,
) -> Dict[str, Any]:
    """Call the VLM and return a normalised payload shared across endpoints."""

    if not file_path:
        raise ValueError("file_path is required for normalisation")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(os.fspath(path))

    image_bytes = path.read_bytes()

    prompt = _force_json_prompt()
    start = time.perf_counter()
    raw_result = await vlm_runner.generate(
        image_bytes=image_bytes,
        prompt=prompt,
        task=VLMRunner.Task.CAPTION,
    )
    latency_ms = raw_result.get("latency_ms")
    if not isinstance(latency_ms, int):
        latency_ms = int((time.perf_counter() - start) * 1000)

    output = str(raw_result.get("output") or raw_result.get("response") or "")
    parsed = _parse_json_output(output)

    image_payload = parsed.get("image") if isinstance(parsed.get("image"), dict) else {}
    resolved_image_id = image_id or image_payload.get("image_id")
    if not resolved_image_id:
        resolved_image_id = _derive_image_id(str(path))

    modality = image_payload.get("modality") or parsed.get("modality")

    report_block = parsed.get("report") if isinstance(parsed.get("report"), dict) else {}
    caption_text = (
        parsed.get("caption")
        or report_block.get("text")
        or output.strip()
    )
    caption_text = caption_text.strip() if isinstance(caption_text, str) else ""

    model_name = (
        report_block.get("model")
        if isinstance(report_block.get("model"), str)
        else None
    ) or raw_result.get("model") or vlm_runner.model

    report_conf = _clamp_conf(report_block.get("conf"))
    if report_conf is None:
        report_conf = _clamp_conf(parsed.get("confidence")) or 0.8

    report_ts = report_block.get("ts")
    if isinstance(report_ts, datetime):
        report_ts_str = report_ts.astimezone(timezone.utc).isoformat()
    elif isinstance(report_ts, str) and report_ts:
        report_ts_str = report_ts
    else:
        report_ts_str = datetime.now(timezone.utc).isoformat()

    report_id = report_block.get("id")
    if not report_id:
        report_id = _derive_report_id(resolved_image_id, caption_text or output, model_name)

    findings_raw = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    findings = _normalise_findings(findings_raw, resolved_image_id)

    fallback_registry_hit = False
    fallback_candidates: List[Dict[str, Any]] = []
    fallback_strategy: Optional[str] = None
    fallback_used = False
    if not findings or force_dummy_fallback:
        fallback_candidates, fallback_registry_hit = _fallback_findings_from_caption(
            caption_text,
            resolved_image_id,
        )
        if fallback_candidates:
            fallback_strategy = (
                "mock_seed" if fallback_registry_hit else fallback_candidates[0].get("source") or "caption_keywords"
            )
            findings = _normalise_findings(fallback_candidates, resolved_image_id)
            fallback_used = True
        elif not findings:
            findings = []

    caption_ko_raw = parsed.get("caption_ko")
    caption_ko = None
    if isinstance(caption_ko_raw, str) and caption_ko_raw.strip():
        caption_ko = clamp_one_line(caption_ko_raw.strip(), 120)

    normalized = {
        "image": {
            "image_id": resolved_image_id,
            "path": str(path),
            "modality": modality,
        },
        "report": {
            "id": report_id,
            "text": caption_text or output,
            "model": model_name,
            "conf": report_conf,
            "ts": report_ts_str,
        },
        "findings": findings,
        "caption": caption_text or output,
        "caption_ko": caption_ko,
        "vlm_latency_ms": latency_ms,
        "raw_vlm": raw_result,
    }
    normalized["finding_fallback"] = {
        "used": fallback_used,
        "registry_hit": fallback_registry_hit,
        "strategy": fallback_strategy if fallback_used else None,
        "force": force_dummy_fallback,
    }
    return normalized


__all__ = [
    "normalize_from_vlm",
    "_force_json_prompt",
    "_derive_image_id",
    "_derive_report_id",
    "_derive_finding_id",
    "_fallback_findings_from_caption",
    "clamp_one_line",
]
