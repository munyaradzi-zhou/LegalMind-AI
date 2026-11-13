"""Shared helpers for the evaluation scripts: dataset loading, statistics and reports.

Reports are written to tests/evaluation/results/<UTC timestamp>/ as JSON (for tooling)
and Markdown (for reading), and the Markdown is printed to the console.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASETS = Path(__file__).parent / "datasets"
RESULTS = Path(__file__).parent / "results"
SAMPLE_DATA = Path(__file__).resolve().parents[2] / "sample_data"

_RUN_ID = os.getenv("EVAL_RUN_ID") or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def load_dataset(name: str) -> list[dict[str, Any]]:
    """Return the `queries` list of a golden dataset in datasets/."""
    with (DATASETS / name).open(encoding="utf-8") as f:
        queries: list[dict[str, Any]] = json.load(f)["queries"]
    return queries


def normalise(text: str) -> str:
    """Lower-case and collapse whitespace, for robust substring checks."""
    return " ".join(text.lower().split())


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (0 for an empty list)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def markdown_table(rows: list[dict[str, Any]]) -> str:
    """Render a list of dicts (same keys) as a Markdown table."""
    if not rows:
        return "_no rows_\n"
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row[h]) for h in headers) + " |")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_report(name: str, summary: dict[str, Any], details: list[dict[str, Any]], markdown: str) -> Path:
    """Save JSON + Markdown reports for one evaluation and print the Markdown."""
    folder = RESULTS / _RUN_ID
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"evaluation": name, "run_id": _RUN_ID, "summary": summary, "details": details}
    (folder / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / f"{name}.md").write_text(markdown, encoding="utf-8")
    print(markdown)  # noqa: T201 — the report is the script's output
    print(f"Saved to {folder / name}.md / .json")  # noqa: T201
    return folder
