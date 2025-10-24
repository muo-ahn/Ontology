"""Case-study dumper for the V/VL/VGL pipelines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    raise SystemExit("install httpx to run this script (pip install httpx)") from exc


DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 60.0


def _read_ids(path: Path) -> List[str]:
    if not path.exists():
        raise SystemExit(f"ids file not found: {path}")
    ids: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            ids.append(stripped)
    if not ids:
        raise SystemExit(f"no image ids found in {path}")
    return ids


def _ensure_parent(path: Path) -> None:
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def _extract_text(payload: object) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (list, tuple)):
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if isinstance(payload, dict):
        for key in ("answer", "response", "output", "text", "context"):
            value = payload.get(key)  # type: ignore[call-arg]
            if value:
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False, indent=2)
                return str(value)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return repr(payload)


def _render_code_block(lines: Iterable[str]) -> List[str]:
    content = ["```text"]
    content.extend(lines)
    content.append("```")
    return content


def _get_graph_context(client: httpx.Client, base_url: str, image_id: str, k: int, timeout: float) -> str:
    params = {"image_id": image_id, "mode": "triples", "k": k}
    response = client.get(f"{base_url}/graph/context", params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return _extract_text(data)


def _call_llm(client: httpx.Client, base_url: str, payload: dict, timeout: float) -> str:
    response = client.post(f"{base_url}/llm/answer", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return _extract_text(data)


def dump_case_studies(
    *,
    base_url: str,
    ids: Iterable[str],
    output_path: Path,
    k: int,
    max_chars: int,
    timeout: float,
) -> None:
    _ensure_parent(output_path)
    md_lines: List[str] = []

    with httpx.Client() as client:
        for image_id in ids:
            md_lines.append(f"### {image_id}")
            md_lines.append("")

            try:
                context_text = _get_graph_context(client, base_url, image_id, k, timeout)
            except httpx.HTTPError as exc:
                context_text = f"[error] {exc}"
            md_lines.append("**GRAPH CONTEXT (triples)**  ")
            md_lines.extend(_render_code_block(context_text.splitlines() or [context_text]))

            # V mode
            v_payload = {"mode": "V", "image_id": image_id, "max_chars": max_chars}
            try:
                v_text = _call_llm(client, base_url, v_payload, timeout)
            except httpx.HTTPError as exc:
                v_text = f"[error] {exc}"
            md_lines.append("**MODE V**  ")
            md_lines.extend(_render_code_block(v_text.splitlines() or [v_text]))

            # VL mode
            vl_payload = {"mode": "VL", "image_id": image_id, "max_chars": max_chars}
            try:
                vl_text = _call_llm(client, base_url, vl_payload, timeout)
            except httpx.HTTPError as exc:
                vl_text = f"[error] {exc}"
            md_lines.append("**MODE VL**  ")
            md_lines.extend(_render_code_block(vl_text.splitlines() or [vl_text]))

            # VGL mode
            vgl_payload = {
                "mode": "VGL",
                "image_id": image_id,
                "max_chars": max_chars,
                "fallback_to_vl": False,
            }
            try:
                vgl_text = _call_llm(client, base_url, vgl_payload, timeout)
            except httpx.HTTPError as exc:
                vgl_text = f"[error] {exc}"
            md_lines.append("**MODE VGL**  ")
            md_lines.extend(_render_code_block(vgl_text.splitlines() or [vgl_text]))

            md_lines.append("")

    output_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump case studies for V/VL/VGL outputs")
    parser.add_argument("ids_file", help="Path to the text file with one image_id per line")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Base URL for the FastAPI service")
    parser.add_argument("--out", default="docs/case_studies.md", help="Output Markdown path")
    parser.add_argument("--k", type=int, default=3, help="Top-k value for graph context retrieval")
    parser.add_argument("--max-chars", type=int, default=256, help="Character limit for LLM responses")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    ids_path = Path(args.ids_file).expanduser()
    ids = _read_ids(ids_path)
    output_path = Path(args.out).expanduser()

    dump_case_studies(
        base_url=args.api_url.rstrip("/"),
        ids=ids,
        output_path=output_path,
        k=args.k,
        max_chars=args.max_chars,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
