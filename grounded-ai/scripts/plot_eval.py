"""Plot helper to visualise evaluation metrics from results.csv."""

from __future__ import annotations

import argparse
import csv
import struct
import zlib
from pathlib import Path
from typing import Iterable

try:  # Prefer matplotlib when available
    import matplotlib.pyplot as plt
    import pandas as pd
    HAVE_MATPLOTLIB = True
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    HAVE_MATPLOTLIB = False


def _load_summary(input_path: Path) -> list[dict[str, float]]:
    if HAVE_MATPLOTLIB:
        df = pd.read_csv(input_path)
        if df.empty:
            raise SystemExit("results CSV is empty")
        grouped = df.groupby("mode").agg(
            factuality=("factuality", "mean"),
            consistency=("consistency", "mean"),
            hallucination=("hallucination", "mean"),
            latency_ms=("latency_ms", "mean"),
        ).reset_index()
        return grouped.to_dict(orient="records")

    # Lightweight fallback using only the standard library.
    with input_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise SystemExit("results CSV is empty")

    summary: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for row in rows:
        mode = row["mode"]
        summary.setdefault(mode, {"factuality": 0.0, "consistency": 0.0, "hallucination": 0.0, "latency_ms": 0.0})
        counts[mode] = counts.get(mode, 0) + 1
        for key in ("factuality", "consistency", "hallucination", "latency_ms"):
            summary[mode][key] += float(row[key])
    records = []
    for mode, metrics in summary.items():
        divisor = counts[mode]
        records.append(
            {
                "mode": mode,
                "factuality": metrics["factuality"] / divisor,
                "consistency": metrics["consistency"] / divisor,
                "hallucination": metrics["hallucination"] / divisor,
                "latency_ms": metrics["latency_ms"] / divisor,
            }
        )
    return records


def _render_with_matplotlib(records: list[dict[str, float]], output_path: Path) -> None:
    df = pd.DataFrame(records)
    fig, axes = plt.subplots(2, 2, figsize=(10, 6))
    metrics = [
        ("factuality", "Factuality"),
        ("consistency", "Consistency"),
        ("hallucination", "Hallucination"),
        ("latency_ms", "Latency (ms)"),
    ]
    for ax, (col, title) in zip(axes.flat, metrics):
        ax.bar(df["mode"], df[col], color="#2a9d8f")
        upper = df[col].max()
        ax.set_title(title)
        ax.set_ylim(0, max(1.0, upper * 1.1) if col != "latency_ms" else max(10.0, upper * 1.2))
        ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)


FONT = {
    " ": [0, 0, 0, 0, 0, 0, 0],
    "-": [0, 0, 0b11100, 0, 0, 0, 0],
    ".": [0, 0, 0, 0, 0, 0, 0b01000],
    "0": [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "2": [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    "3": [0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110],
    "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    "5": [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    "6": [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
    ">": [0b10000, 0b01000, 0b00100, 0b00010, 0b00100, 0b01000, 0b10000],
    "V": [0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b01010, 0b00100],
    "G": [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110],
    "L": [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
    "F": [0b11111, 0b10000, 0b11100, 0b10000, 0b10000, 0b10000, 0b10000],
    "A": [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "C": [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
    "T": [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
    "U": [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "I": [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "Y": [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
    "O": [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "N": [0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001],
    "S": [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
    "H": [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "M": [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
    "E": [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
    "D": [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
    "P": [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
    "R": [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
    "_": [0, 0, 0, 0, 0, 0, 0b11111],
}

FONT_WIDTH = 5
FONT_HEIGHT = 7
H_PADDING = 2
V_PADDING = 2


def _draw_text_lines(lines: Iterable[str]) -> list[list[int]]:
    pixel_rows: list[list[int]] = []
    for line_index, line in enumerate(lines):
        if line_index == 0:
            pixel_rows.extend([[255] * (H_PADDING + len(line) * (FONT_WIDTH + 1)) for _ in range(V_PADDING)])
        row_pixels = [[255] * (H_PADDING + len(line) * (FONT_WIDTH + 1)) for _ in range(FONT_HEIGHT)]
        for char_index, char in enumerate(line):
            glyph = FONT.get(char.upper(), FONT[" "])
            for y in range(FONT_HEIGHT):
                bits = glyph[y]
                for x in range(FONT_WIDTH):
                    if bits & (1 << (FONT_WIDTH - 1 - x)):
                        row_pixels[y][H_PADDING + char_index * (FONT_WIDTH + 1) + x] = 0
        pixel_rows.extend(row_pixels)
        pixel_rows.append([255] * (H_PADDING + len(line) * (FONT_WIDTH + 1)))
    return pixel_rows


def _write_png(pixels: list[list[int]], output_path: Path) -> None:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    output_path.write_bytes(png_bytes)


def _render_fallback(records: list[dict[str, float]], output_path: Path) -> None:
    lines = ["MODE  FACTUALITY  CONSISTENCY  HALLUCINATION  LATENCY_MS"]
    for row in records:
        line = (
            f"{row['mode']:<8}"
            f"{row['factuality']:>10.2f}"
            f"{row['consistency']:>12.2f}"
            f"{row['hallucination']:>14.2f}"
            f"{row['latency_ms']:>12.2f}"
        )
        line = line.replace("→", "->")
        lines.append(line)
    pixels = _draw_text_lines(lines)
    _write_png(pixels, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot evaluation metrics")
    parser.add_argument("--input", default="grounded-ai/results/results.csv", help="Path to results.csv")
    parser.add_argument("--output", default="grounded-ai/results/results_summary.png", help="Output image path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"results CSV not found: {input_path}")

    records = _load_summary(input_path)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if HAVE_MATPLOTLIB:
        _render_with_matplotlib(records, output_path)
    else:
        _render_fallback(records, output_path)

    print(f"Saved plot to {output_path}")


if __name__ == "__main__":
    main()
