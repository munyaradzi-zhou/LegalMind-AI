"""Evaluation gate: compare a run with the baseline on main and fail on a real drop.

Reads the retrieval and generation reports of one run (tests/evaluation/results/<run>/),
compares the gated metrics with tests/evaluation/baselines/main.json and writes a Markdown
table (the pull request comment). Exits 1 when a gated metric falls below its baseline by
more than its tolerance.

Tolerances absorb run-to-run noise: answers vary between runs at temperature 0 because
retrieval scores and model outputs aren't perfectly deterministic. Unanswerable questions
have no tolerance: answering one without a warning is never acceptable. The LLM judge's
score and latency are shown but not gated (the judge is noisy; latency depends on load).

    python -m tests.evaluation.gate --run tests/evaluation/results/<run>
    python -m tests.evaluation.gate --run tests/evaluation/results/<run> --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASELINE = Path(__file__).parent / "baselines" / "main.json"


@dataclass(frozen=True)
class Metric:
    key: str  # "<report>.<field>"
    label: str
    tolerance: float | None  # None = reported, not gated
    higher_is_better: bool = True


METRICS = [
    Metric("retrieval.hit@3", "Retrieval: right passage in top 3", 0.03),
    Metric("retrieval.hit@5", "Retrieval: right passage in top 5", 0.03),
    Metric("retrieval.mrr@10", "Retrieval: MRR@10", 0.03),
    Metric("generation.fact_recall", "Answers: expected facts present", 0.03),
    Metric("generation.all_facts_present", "Answers: all facts present", 0.05),
    Metric("generation.right_source_cited", "Answers: right source cited", 0.05),
    Metric("generation.unanswerable_safe", "Unanswerable: declined or flagged", 0.0),
    Metric("generation.groundedness_mean", "Judge: groundedness (1-5, noisy)", None),
    Metric("generation.latency_p50_s", "Latency p50 (s)", None, higher_is_better=False),
    Metric("generation.latency_p95_s", "Latency p95 (s)", None, higher_is_better=False),
]


def read_run(run: Path) -> dict[str, Any]:
    """Metric values and prompt fingerprint from one run's reports."""
    values: dict[str, Any] = {}
    retrieval = json.loads((run / "retrieval.json").read_text(encoding="utf-8"))["summary"]
    production = next(m for m in retrieval["modes"] if m["mode"] == "production")
    for field in ("hit@1", "hit@3", "hit@5", "mrr@10", "source@1"):
        values[f"retrieval.{field}"] = production[field]
    generation = json.loads((run / "generation.json").read_text(encoding="utf-8"))["summary"]
    for field, value in generation.items():
        if isinstance(value, int | float):
            values[f"generation.{field}"] = value
    return {"metrics": values, "prompts": generation.get("prompts", "")}


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Rows for the comment table, and the labels of the metrics that failed."""
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    for metric in METRICS:
        now = current["metrics"].get(metric.key)
        before = baseline["metrics"].get(metric.key)
        if now is None or before is None:
            rows.append({"metric": metric.label, "main": _fmt(before), "this PR": _fmt(now), "change": "", "": "-"})
            continue
        change = now - before
        worse = -change if metric.higher_is_better else change
        if metric.tolerance is None:
            status = "info"
        elif worse > metric.tolerance + 1e-9:
            status = "FAIL"
            failed.append(metric.label)
        else:
            status = "ok"
        rows.append(
            {"metric": metric.label, "main": _fmt(before), "this PR": _fmt(now), "change": f"{change:+.3f}", "": status}
        )
    return rows, failed


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def report(rows: list[dict[str, str]], failed: list[str], current: dict[str, Any], baseline: dict[str, Any]) -> str:
    header = "| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]) + "\n"
    table = header + "".join("| " + " | ".join(r.values()) + " |\n" for r in rows)
    prompts = (
        "Prompts unchanged."
        if current["prompts"] == baseline.get("prompts")
        else f"**Prompts changed**: `{baseline.get('prompts', '-')}` → `{current['prompts']}`."
    )
    verdict = (
        f"**Evaluation gate failed**: {', '.join(failed)} dropped beyond tolerance."
        if failed
        else "**Evaluation gate passed.**"
    )
    return (
        "## Evaluation gate\n\n"
        f"{verdict} {prompts}\n\n{table}\n"
        f"Baseline: `{baseline.get('commit', '-')}` ({baseline.get('recorded', '-')}). "
        "Gated metrics fail when they drop by more than their tolerance; `info` rows are not gated.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare an evaluation run with the baseline")
    parser.add_argument("--run", type=Path, required=True, help="results folder of the run")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--comment", type=Path, help="write the Markdown report here")
    parser.add_argument("--update-baseline", action="store_true", help="make this run the new baseline")
    parser.add_argument("--commit", default="", help="commit the run was made on (recorded in the baseline)")
    args = parser.parse_args()

    current = read_run(args.run)
    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "commit": args.commit,
            "recorded": datetime.now(UTC).strftime("%Y-%m-%d"),
            "run": args.run.name,
            **current,
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline updated from {args.run}")  # noqa: T201
        return

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    rows, failed = compare(current, baseline)
    markdown = report(rows, failed, current, baseline)
    print(markdown)  # noqa: T201
    if args.comment:
        args.comment.write_text(markdown, encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
