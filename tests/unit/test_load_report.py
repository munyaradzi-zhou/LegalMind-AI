"""The load test report: per-stage numbers and where it breaks (tests/load/report.py)."""

from __future__ import annotations

from tests.load.report import markdown, summarise


def rows(users: int, start: float, latencies: list[int], failed: int = 0) -> list[dict[str, str]]:
    ok = [
        {"time": str(start + i), "users": str(users), "ok": "1", "latency_ms": str(ms), "grounding": "documents"}
        for i, ms in enumerate(latencies)
    ]
    bad = [
        {"time": str(start + len(ok) + i), "users": str(users), "ok": "0", "latency_ms": "0", "grounding": ""}
        for i in range(failed)
    ]
    return ok + bad


def test_each_stage_gets_its_own_percentiles() -> None:
    stages = summarise(rows(2, 0, [10_000] * 19 + [40_000]) + rows(5, 100, [20_000] * 10))
    assert [s["users"] for s in stages] == [2, 5]
    assert stages[0]["p50"] == 10.0 and stages[0]["p99"] == 40.0
    assert stages[1]["p95"] == 20.0 and stages[1]["error_rate"] == 0.0


def test_the_first_stage_with_errors_is_the_breaking_point() -> None:
    report = markdown(summarise(rows(5, 0, [15_000] * 50) + rows(20, 100, [25_000] * 45, failed=5)))
    assert "Breaks at **20 concurrent users**" in report
    assert "| 20 | 50 |" in report and "10.0%" in report


def test_slow_answers_break_it_too() -> None:
    report = markdown(summarise(rows(30, 0, [70_000] * 20)))
    assert "Breaks at **30 concurrent users**" in report


def test_a_run_that_holds() -> None:
    assert "Held at every stage" in markdown(summarise(rows(10, 0, [12_000] * 30)))
