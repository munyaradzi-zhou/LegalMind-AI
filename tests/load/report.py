"""Summarise a load test run per stage: throughput, latency percentiles, errors, breaking point.

    python -m tests.load.report load_requests.csv [--out report.md]

Reads the per-request CSV that tests/load/locustfile.py writes. A stage "breaks" when more
than 1% of its requests fail or its p95 latency exceeds 60 s (users would give up).
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

ERROR_LIMIT = 0.01
P95_LIMIT_S = 60.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def summarise(rows: list[dict[str, str]]) -> list[dict[str, float]]:
    by_users: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_users[int(row["users"])].append(row)
    stages = []
    for users in sorted(by_users):
        stage = by_users[users]
        seconds = max(1.0, float(stage[-1]["time"]) - float(stage[0]["time"]))
        latencies = [int(r["latency_ms"]) / 1000 for r in stage if r["ok"] == "1"]
        failed = sum(r["ok"] != "1" for r in stage)
        unverified = sum(r["grounding"] == "unverified" for r in stage)
        stages.append(
            {
                "users": users,
                "requests": len(stage),
                "per_min": len(stage) / seconds * 60,
                "p50": statistics.median(latencies) if latencies else 0.0,
                "p95": percentile(latencies, 95),
                "p99": percentile(latencies, 99),
                "error_rate": failed / len(stage),
                "unverified": unverified / len(stage),
            }
        )
    return stages


def markdown(stages: list[dict[str, float]]) -> str:
    lines = [
        "| users | requests | answers/min | p50 (s) | p95 (s) | p99 (s) | errors | unverified | |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    broke = None
    for s in stages:
        breaks = s["error_rate"] > ERROR_LIMIT or s["p95"] > P95_LIMIT_S
        if breaks and broke is None:
            broke = int(s["users"])
        lines.append(
            f"| {int(s['users'])} | {int(s['requests'])} | {s['per_min']:.1f} | {s['p50']:.1f} | {s['p95']:.1f} "
            f"| {s['p99']:.1f} | {s['error_rate']:.1%} | {s['unverified']:.1%} | {'breaks' if breaks else 'ok'} |"
        )
    verdict = (
        f"Breaks at **{broke} concurrent users** (more than {ERROR_LIMIT:.0%} errors or p95 above {P95_LIMIT_S:.0f} s)."
        if broke
        else f"Held at every stage (errors at most {ERROR_LIMIT:.0%}, p95 at most {P95_LIMIT_S:.0f} s)."
    )
    return "## Load test\n\n" + "\n".join(lines) + f"\n\n{verdict}\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    with args.csv.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["name"] == "/api/chat"]
    report = markdown(summarise(rows))
    print(report)  # noqa: T201
    if args.out:
        args.out.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
