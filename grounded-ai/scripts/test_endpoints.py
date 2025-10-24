"""Smoke-test script covering the health, vision, graph, and LLM endpoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict
import base64

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    raise SystemExit("install httpx to run this script (pip install httpx)") from exc


def _print_section(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def _ensure_image_id(path: Path) -> str:
    stem = path.stem.upper()
    if not stem.startswith("IMG"):
        return f"IMG_{stem}"
    return stem


def _build_upsert_payload(caption: Dict[str, Any], case_id: str, default_path: Path) -> Dict[str, Any]:
    image_block = dict(caption.get("image", {}))
    image_block.setdefault("id", image_block.get("image_id"))
    image_block.setdefault("path", f"/data/{default_path.name}")

    report_block = dict(caption.get("report", {}))
    findings_block = list(caption.get("findings", []))

    return {
        "case_id": case_id,
        "image": image_block,
        "report": report_block,
        "findings": findings_block,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test all FastAPI endpoints")
    parser.add_argument("--api-url", default="http://localhost:8000", help="FastAPI base URL")
    parser.add_argument(
        "--image",
        default="grounded-ai/data/medical_dummy/images/img_001.png",
        help="Path to the sample image used for the test run",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout (seconds)")
    parser.add_argument("--case-id", default=None, help="Override case identifier for graph upserts")
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        raise SystemExit(f"image not found: {image_path}")

    image_id = _ensure_image_id(image_path)
    case_id = args.case_id or f"C_{image_id}"
    base_url = args.api_url.rstrip("/")

    with httpx.Client(base_url=base_url, timeout=args.timeout) as client:
        # 1. /health
        health_resp = client.get("/health")
        health_resp.raise_for_status()
        _print_section("GET /health", health_resp.json())

        # 2. /vision/caption
        image_bytes = image_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        caption_payload = {
            "image_b64": image_b64,
            "image_id": image_id,
            "case_id": case_id,
        }
        caption_resp = client.post("/vision/caption", json=caption_payload)
        caption_resp.raise_for_status()
        caption_data = caption_resp.json()
        _print_section("POST /vision/caption", caption_data)

        # 3. /graph/upsert
        upsert_payload = _build_upsert_payload(caption_data, case_id, image_path)
        upsert_resp = client.post("/graph/upsert", json=upsert_payload)
        upsert_resp.raise_for_status()
        _print_section("POST /graph/upsert", upsert_resp.json())

        # 4. /graph/context (triples + json)
        context_resp = client.get("/graph/context", params={"image_id": image_id, "mode": "triples", "k": 2})
        context_resp.raise_for_status()
        _print_section("GET /graph/context (mode=triples)", context_resp.json())

        context_json_resp = client.get("/graph/context", params={"image_id": image_id, "mode": "json", "k": 2})
        context_json_resp.raise_for_status()
        _print_section("GET /graph/context (mode=json)", context_json_resp.json())

        # 5. /llm/answer
        caption_text = caption_data.get("report", {}).get("text", "")

        v_resp = client.post(
            "/llm/answer",
            json={"mode": "V", "image_id": image_id, "caption": caption_text, "style": "one_line"},
        )
        v_resp.raise_for_status()
        _print_section("POST /llm/answer (mode=V)", v_resp.json())

        vl_payload = {"mode": "VL", "image_id": image_id, "caption": caption_text, "style": "one_line"}
        vl_resp = client.post("/llm/answer", json=vl_payload)
        vl_resp.raise_for_status()
        _print_section("POST /llm/answer (mode=VL)", vl_resp.json())

        vgl_resp = client.post(
            "/llm/answer",
            json={"mode": "VGL", "image_id": image_id, "style": "one_line"},
        )
        vgl_resp.raise_for_status()
        _print_section("POST /llm/answer (mode=VGL)", vgl_resp.json())

    print("\nAll endpoints responded successfully ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
