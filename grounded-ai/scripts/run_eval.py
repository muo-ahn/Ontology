"""Batch evaluation script comparing V, VL, and VGL pipelines."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit("install httpx to run the evaluation script (pip install httpx)") from exc


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "medical_dummy"
IMAGES_DIR_DEFAULT = DATA_DIR / "images"
RESULTS_DIR_DEFAULT = ROOT / "results"
GROUND_TRUTH_FILE = DATA_DIR / "ground_truth.json"
PLOT_SCRIPT = ROOT / "scripts" / "plot_eval.py"

PIPELINE_MODES = ("V", "VL", "VGL")


def _load_ground_truth() -> Dict[str, Dict]:
    if not GROUND_TRUTH_FILE.exists():
        raise SystemExit(f"ground truth file missing: {GROUND_TRUTH_FILE}")
    raw = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    records: Dict[str, Dict] = {}
    for item in raw:
        id = (item.get("id") or item.get("id") or "").upper()
        if not id:
            continue
        item["id"] = id
        records[id] = item
    return records


def _normalise_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _jaccard(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _consistency(outputs: List[str]) -> float:
    if len(outputs) <= 1:
        return 1.0
    pairs = [_jaccard(a, b) for a, b in combinations(outputs, 2)]
    return statistics.mean(pairs) if pairs else 1.0


def _factuality(entry: Dict | None, text: str) -> float:
    if not entry:
        return 0.0
    keywords = [kw.lower() for kw in entry.get("keywords", [])]
    if not keywords:
        return 1.0
    text_lower = text.lower()
    return 1.0 if all(keyword in text_lower for keyword in keywords) else 0.0


def _hallucination(entry: Dict | None, text: str) -> float:
    if not entry:
        return 0.0
    guards = [term.lower() for term in entry.get("blacklist", [])]
    text_lower = text.lower()
    return 1.0 if any(term in text_lower for term in guards) else 0.0


def _call_caption(
    client: httpx.Client,
    base_url: str,
    image_path: Path,
    id: str,
    case_id: str | None,
    timeout: float,
) -> Dict:
    payload = {
        "file_path": str(image_path.resolve()),
        "id": id,
        "case_id": case_id,
    }
    start = time.perf_counter()
    response = client.post(f"{base_url}/vision/caption", json=payload, timeout=timeout)
    response.raise_for_status()
    latency_ms = int((time.perf_counter() - start) * 1000)
    data = response.json()
    data["_latency_ms"] = latency_ms
    return data


def _call_llm_answer(
    client: httpx.Client,
    base_url: str,
    payload: Dict,
    timeout: float,
) -> Dict:
    start = time.perf_counter()
    response = client.post(f"{base_url}/llm/answer", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    latency_ms = data.get("latency_ms")
    if latency_ms is None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        data["latency_ms"] = latency_ms
    return data


def _call_graph_upsert(client: httpx.Client, base_url: str, payload: Dict, timeout: float) -> None:
    response = client.post(f"{base_url}/graph/upsert", json=payload, timeout=timeout)
    response.raise_for_status()


def _call_graph_context(client: httpx.Client, base_url: str, id: str, timeout: float) -> str:
    params = {"id": id, "k": 2, "mode": "triples"}
    response = client.get(f"{base_url}/graph/context", params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("context", "")


def _evaluate_mode(outputs: List[str], latencies: List[float], entry: Dict | None, force_consistency: float | None = None) -> Dict[str, float]:
    outputs_norm = [_normalise_text(text) for text in outputs if text is not None]
    if not outputs_norm:
        outputs_norm = [""]
    factual_scores = [_factuality(entry, text) for text in outputs_norm]
    halluc_scores = [_hallucination(entry, text) for text in outputs_norm]
    factuality = float(max(factual_scores)) if factual_scores else 0.0
    hallucination = float(max(halluc_scores)) if halluc_scores else 0.0
    consistency = force_consistency if force_consistency is not None else float(_consistency(outputs_norm))
    latency_ms = float(statistics.mean(latencies)) if latencies else 0.0
    return {
        "factuality": factuality,
        "hallucination": hallucination,
        "consistency": consistency,
        "latency_ms": latency_ms,
        "outputs": outputs_norm,
    }


def _prepare_upsert_payload(entry: Dict | None, caption_data: Dict, image_path: Path, case_id: str) -> Dict:
    image_block = dict(caption_data.get("image", {}))
    image_block["id"] = image_block.get("id") or image_block.get("id")
    image_block["path"] = str(image_path.resolve())
    image_block.setdefault("modality", (entry or {}).get("modality"))

    report_block = dict(caption_data.get("report", {}))
    if "ts" in report_block:
        report_block["ts"] = str(report_block["ts"])

    findings_block = list(caption_data.get("findings", []))

    return {
        "case_id": case_id,
        "image": image_block,
        "report": report_block,
        "findings": findings_block,
    }


def _case_id_for(entry: Dict | None, id: str) -> str:
    if entry and entry.get("case_id"):
        return entry["case_id"]
    return f"C_{id}"


def evaluate_image(
    client: httpx.Client,
    *,
    base_url: str,
    image_path: Path,
    entry: Dict | None,
    repeats: int,
    timeout: float,
) -> List[Dict[str, float]]:
    id = (entry or {}).get("id") or image_path.stem.upper()

    case_id = _case_id_for(entry, id)
    caption_data = _call_caption(client, base_url, image_path, id, case_id, timeout)
    caption_text = _normalise_text(caption_data["report"]["text"])

    rows: List[Dict[str, float]] = []

    v_metrics = _evaluate_mode([caption_text], [caption_data["_latency_ms"]], entry, force_consistency=1.0)
    rows.append(
        {
            "id": id,
            "mode": "V",
            "factuality": v_metrics["factuality"],
            "hallucination": v_metrics["hallucination"],
            "consistency": v_metrics["consistency"],
            "latency_ms": v_metrics["latency_ms"],
        }
    )

    vl_outputs: List[str] = []
    vl_latencies: List[float] = []
    for _ in range(repeats):
        llm_payload = {"mode": "VL", "id": id, "caption": caption_text, "style": "one_line"}
        llm_response = _call_llm_answer(client, base_url, llm_payload, timeout)
        vl_outputs.append(llm_response.get("answer", ""))
        vl_latencies.append(float(llm_response.get("latency_ms", 0)))
    vl_metrics = _evaluate_mode(vl_outputs, vl_latencies, entry)
    rows.append(
        {
            "id": id,
            "mode": "VL",
            "factuality": vl_metrics["factuality"],
            "hallucination": vl_metrics["hallucination"],
            "consistency": vl_metrics["consistency"],
            "latency_ms": vl_metrics["latency_ms"],
        }
    )

    vgl_outputs: List[str] = []
    vgl_latencies: List[float] = []
    upsert_payload = _prepare_upsert_payload(entry, caption_data, image_path, case_id)
    for _ in range(repeats):
        run_start = time.perf_counter()
        _call_graph_upsert(client, base_url, upsert_payload, timeout)
        _call_graph_context(client, base_url, id, timeout)
        llm_response = _call_llm_answer(
            client,
            base_url,
            {"mode": "VGL", "id": id, "style": "one_line"},
            timeout,
        )
        vgl_outputs.append(llm_response.get("answer", ""))
        elapsed_ms = int((time.perf_counter() - run_start) * 1000)
        vgl_latencies.append(float(elapsed_ms))
    vgl_metrics = _evaluate_mode(vgl_outputs, vgl_latencies, entry)
    rows.append(
        {
            "id": id,
            "mode": "VGL",
            "factuality": vgl_metrics["factuality"],
            "hallucination": vgl_metrics["hallucination"],
            "consistency": vgl_metrics["consistency"],
            "latency_ms": vgl_metrics["latency_ms"],
        }
    )
    return rows


def _summarise(rows: Iterable[Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    summary: Dict[str, Dict[str, float]] = {}
    counts: Dict[str, int] = {}
    for row in rows:
        mode = row["mode"]
        summary.setdefault(mode, {"factuality": 0.0, "hallucination": 0.0, "consistency": 0.0, "latency_ms": 0.0})
        counts[mode] = counts.get(mode, 0) + 1
        summary[mode]["factuality"] += float(row["factuality"])
        summary[mode]["hallucination"] += float(row["hallucination"])
        summary[mode]["consistency"] += float(row["consistency"])
        summary[mode]["latency_ms"] += float(row["latency_ms"])
    for mode, metrics in summary.items():
        divisor = counts[mode]
        summary[mode] = {k: (v / divisor if divisor else 0.0) for k, v in metrics.items()}
    return summary


def _maybe_plot(results_csv: Path, output_dir: Path) -> None:
    if not PLOT_SCRIPT.exists():
        print(f"[WARN] plot script not found: {PLOT_SCRIPT}", file=sys.stderr)
        return
    output_path = output_dir / "results_summary.png"
    try:
        subprocess.run(
            [
                sys.executable,
                str(PLOT_SCRIPT),
                "--input",
                str(results_csv),
                "--output",
                str(output_path),
            ],
            check=True,
        )
    except Exception as exc:  # pragma: no cover - plotting optional
        print(f"[WARN] plot generation failed: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch evaluation for V / VL / VGL pipelines")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Base URL for the FastAPI service")
    parser.add_argument("--images-dir", default=str(IMAGES_DIR_DEFAULT), help="Directory containing evaluation images")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR_DEFAULT), help="Directory to store results.csv/summary.json")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP request timeout (seconds)")
    parser.add_argument("--repeats", type=int, default=3, help="Number of generations per mode (VL/VGL)")
    parser.add_argument("--plot", action="store_true", help="Generate PNG chart via plot_eval.py")
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.exists():
        raise SystemExit(f"images directory not found: {images_dir}")

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    results_csv = results_dir / "results.csv"
    summary_json = results_dir / "summary.json"

    ground_truth = _load_ground_truth()
    images = sorted(images_dir.glob("*.png"))
    if not images:
        raise SystemExit(f"no PNG images found in {images_dir}")

    base_url = args.api_url.rstrip("/")
    print(f"[INFO] evaluating {len(images)} images against {base_url}")

    rows: List[Dict[str, float]] = []
    with httpx.Client(timeout=args.timeout) as client:
        for image_path in images:
            id = image_path.stem.upper()
            entry = ground_truth.get(id)
            try:
                image_rows = evaluate_image(
                    client,
                    base_url=base_url,
                    image_path=image_path,
                    entry=entry,
                    repeats=args.repeats,
                    timeout=args.timeout,
                )
            except Exception as exc:
                print(f"[ERROR] failed to evaluate {image_path.name}: {exc}", file=sys.stderr)
                continue
            rows.extend(image_rows)

    if not rows:
        raise SystemExit("evaluation produced no rows")

    with results_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["id", "mode", "factuality", "hallucination", "consistency", "latency_ms"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    summary = _summarise(rows)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.plot:
        _maybe_plot(results_csv, results_dir)

    print(f"[INFO] saved results to {results_csv}")
    print(f"[INFO] saved summary to {summary_json}")


if __name__ == "__main__":
    main()

