"""Batch evaluation for the three pipeline variants (V, V+L, V→G→L)."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from itertools import combinations
from pathlib import Path
from typing import Iterable

httpx = None

try:  # Optional dependency so --mock still works without httpx installed.
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    httpx = None

# Allow the script to be run via `python scripts/run_eval.py` from the repo root.
ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "api" / "services"
if str(SERVICES_DIR) not in sys.path:
    sys.path.append(str(SERVICES_DIR))
MODELS_DIR = ROOT / "api"
if str(MODELS_DIR) not in sys.path:
    sys.path.append(str(MODELS_DIR))

from dummy_dataset import (  # type: ignore  # noqa: E402  (runtime path setup)
    blacklist_terms,
    default_summary,
    expected_keywords,
    load_ground_truth,
)


PIPELINE_MODES = ["V", "V+L", "V→G→L"]


def _jaccard(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _consistency(outputs: list[str]) -> float:
    if len(outputs) < 2:
        return 1.0
    pairs = [_jaccard(a, b) for a, b in combinations(outputs, 2)]
    return sum(pairs) / len(pairs)


def _factuality(entry: dict, text: str) -> float:
    keywords = [k.lower() for k in expected_keywords(entry)]
    if not keywords:
        return 1.0
    text_lower = text.lower()
    matched = sum(1 for keyword in keywords if keyword in text_lower)
    return matched / len(keywords)


def _hallucination(entry: dict, text: str) -> float:
    guards = [g.lower() for g in blacklist_terms(entry)]
    text_lower = text.lower()
    return 1.0 if any(term in text_lower for term in guards) else 0.0


def _mock_response(entry: dict, mode: str) -> dict:
    summary = default_summary(entry) or entry["caption"]
    if mode == "V":
        output = entry["caption"]
    else:
        output = summary
    return {
        "mode": mode,
        "image_id": entry["image_id"],
        "case_id": entry.get("case_id"),
        "caption": entry["caption"],
        "findings": [finding for finding in entry.get("findings", [])],
        "output": output,
        "timings": {"total_ms": 0, "vlm_ms": 0, "llm_ms": 0, "graph_ms": 0},
    }


def run_case(
    client: "httpx.Client | None",
    *,
    entry: dict,
    base_url: str,
    repeats: int,
    mock: bool,
    timeout: float,
    images_dir: Path,
) -> Iterable[dict[str, float]]:
    image_path = images_dir / entry["file_name"]
    payload = {
        "file_path": str(image_path),
        "case_id": entry.get("case_id"),
    }

    rows: list[dict[str, float]] = []
    for mode in PIPELINE_MODES:
        outputs: list[str] = []
        hallucinations: list[float] = []
        factual_scores: list[float] = []
        latencies: list[float] = []

        for _ in range(repeats):
            if mock:
                response = _mock_response(entry, mode)
            else:
                if client is None:
                    raise RuntimeError("httpx is required for non-mock evaluation")
                response = client.post(
                    f"{base_url}/llm/answer",
                    json={"mode": mode, **payload},
                    timeout=timeout,
                )
                response.raise_for_status()
                response = response.json()
            outputs.append(response.get("output", ""))
            factual_scores.append(_factuality(entry, outputs[-1]))
            hallucinations.append(_hallucination(entry, outputs[-1]))
            timings = response.get("timings", {})
            latencies.append(float(timings.get("total_ms", 0)))

        rows.append(
            {
                "image_id": entry["image_id"],
                "mode": mode,
                "factuality": float(sum(factual_scores) / len(factual_scores)),
                "consistency": float(_consistency(outputs)),
                "hallucination": float(sum(hallucinations) / len(hallucinations)),
                "latency_ms": float(statistics.mean(latencies)) if latencies else 0.0,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate V / V+L / V→G→L pipelines")
    parser.add_argument("--api-url", default="http://localhost:8000", help="FastAPI base URL")
    parser.add_argument("--output", default="grounded-ai/results/results.csv", help="Where to write the CSV")
    parser.add_argument("--repeats", type=int, default=3, help="Number of runs per mode")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")
    parser.add_argument("--mock", action="store_true", help="Use curated dataset outputs instead of the API")
    parser.add_argument(
        "--images-dir",
        default="grounded-ai/data/medical_dummy/images",
        help="Directory containing the sample images",
    )
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.exists():
        raise SystemExit(f"Images directory not found: {images_dir}")

    ground_truth = load_ground_truth()
    if not ground_truth:
        raise SystemExit("ground_truth.json not found – ensure the dummy dataset is present")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.mock and httpx is None:
        raise SystemExit("Install httpx to run the evaluation against the live API (pip install httpx)")

    rows: list[dict[str, float]] = []
    client_ctx = httpx.Client() if httpx is not None else None
    try:
        for entry in ground_truth.values():
            case_rows = run_case(
                client_ctx,
                entry=entry,
                base_url=args.api_url.rstrip("/"),
                repeats=args.repeats,
                mock=args.mock,
                timeout=args.timeout,
                images_dir=images_dir,
            )
            rows.extend(case_rows)
    finally:
        if client_ctx is not None:
            client_ctx.close()

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["image_id", "mode", "factuality", "consistency", "hallucination", "latency_ms"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
