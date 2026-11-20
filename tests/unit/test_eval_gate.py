"""The evaluation gate fails on a real drop and tolerates noise."""

from __future__ import annotations

from tests.evaluation.gate import compare, report

BASELINE = {
    "commit": "abc123",
    "recorded": "2026-11-20",
    "prompts": "p1",
    "metrics": {
        "retrieval.hit@3": 0.88,
        "retrieval.hit@5": 0.93,
        "retrieval.mrr@10": 0.77,
        "generation.fact_recall": 0.90,
        "generation.all_facts_present": 0.80,
        "generation.right_source_cited": 0.95,
        "generation.unanswerable_safe": 1.0,
        "generation.groundedness_mean": 4.5,
        "generation.latency_p50_s": 9.0,
        "generation.latency_p95_s": 20.0,
    },
}


def _run(**changes: float) -> dict[str, object]:
    return {"prompts": "p1", "metrics": {**BASELINE["metrics"], **changes}}  # type: ignore[dict-item]


def test_identical_run_passes() -> None:
    _, failed = compare(_run(), BASELINE)
    assert failed == []


def test_drop_within_tolerance_passes() -> None:
    _, failed = compare(_run(**{"retrieval.hit@3": 0.86, "generation.fact_recall": 0.875}), BASELINE)
    assert failed == []


def test_drop_beyond_tolerance_fails() -> None:
    _, failed = compare(_run(**{"retrieval.hit@3": 0.80}), BASELINE)
    assert failed == ["Retrieval: right passage in top 3"]


def test_any_unsafe_unanswerable_fails() -> None:
    _, failed = compare(_run(**{"generation.unanswerable_safe": 0.95}), BASELINE)
    assert failed == ["Unanswerable: declined or flagged"]


def test_judge_and_latency_are_not_gated() -> None:
    worse = {"generation.groundedness_mean": 3.0, "generation.latency_p95_s": 60.0}
    rows, failed = compare(_run(**worse), BASELINE)
    assert failed == []
    assert {r[""] for r in rows if r["metric"].startswith(("Judge", "Latency"))} == {"info"}


def test_report_mentions_a_prompt_change() -> None:
    current = {**_run(), "prompts": "p2"}
    rows, failed = compare(current, BASELINE)
    text = report(rows, failed, current, BASELINE)
    assert "Prompts changed" in text and "`p1` → `p2`" in text
    assert "Evaluation gate passed" in text
