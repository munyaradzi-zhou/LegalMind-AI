"""Answers that need the live legal sources (datasets/live_sources_golden_set.json), end to end.

For each question, through the live API:

- live_source_cited: a citation links to the expected source (wetten.overheid.nl or rechtspraak.nl)
- identifier_cited:  the answer names the article or ECLI ("7:271", "ECLI:NL:HR:...")
- fact_recall:       facts from the official text that the answer contains
- grounding:         the app's own label; live sources go through the groundedness check too

    API_URL=http://localhost:8000 python -m tests.evaluation.evaluate_live_sources
"""

from __future__ import annotations

import asyncio
import os
import ssl
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import truststore

from tests.evaluation._common import load_dataset, markdown_table, mean, normalise, percentile, write_report

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")


def _fact_recall(answer: str, facts: list[list[str]]) -> tuple[float, list[str]]:
    text = normalise(answer)
    missing = [" / ".join(variants) for variants in facts if not any(normalise(v) in text for v in variants)]
    return (1.0 - len(missing) / len(facts) if facts else 1.0), missing


async def main() -> None:
    queries = load_dataset("live_sources_golden_set.json")
    details: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=240, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as client:
        for q in queries:
            started = time.perf_counter()
            response = await client.post(
                f"{API_URL}/api/chat", json={"message": q["question"], "user_id": "eval-live", "stream": False}
            )
            latency = time.perf_counter() - started
            response.raise_for_status()
            reply = response.json()
            answer = reply["message"]
            citations = reply.get("citations", [])
            live = [c for c in citations if urlparse(c.get("url") or "").hostname == q["host"]]
            named = " ".join([answer, *(c["document_id"] for c in live)])
            recall, missing = _fact_recall(answer, q["must_contain"])
            details.append(
                {
                    "id": q["id"],
                    "tool": q["tool"],
                    "agent": reply.get("agent_name", ""),
                    "live_source_cited": bool(live),
                    "identifier_cited": all(c.lower() in answer.lower() for c in q["cite"]),
                    "identifier_in_citations": all(c.lower() in named.lower() for c in q["cite"]),
                    "fact_recall": recall,
                    "missing": "; ".join(missing),
                    "grounding": reply.get("grounding", ""),
                    "live_citations": ", ".join(c["document_id"] for c in live)[:120],
                    "latency_s": round(latency, 1),
                }
            )
            print(f"{q['id']}: live={bool(live)} recall={recall:.2f} grounding={reply.get('grounding')}", flush=True)  # noqa: T201

    summary = {
        "questions": len(details),
        "live_source_cited": mean([1.0 if d["live_source_cited"] else 0.0 for d in details]),
        "identifier_cited": mean([1.0 if d["identifier_cited"] else 0.0 for d in details]),
        "fact_recall": mean([d["fact_recall"] for d in details]),
        "grounded": mean([1.0 if d["grounding"] == "documents" else 0.0 for d in details]),
        "latency_p50_s": percentile([d["latency_s"] for d in details], 50),
        "latency_p95_s": percentile([d["latency_s"] for d in details], 95),
    }
    markdown = (
        "# Live legal sources (end to end, live API)\n\n## Summary\n\n"
        + markdown_table([summary])
        + "\n## Per question\n\n"
        + markdown_table(details)
    )
    write_report("live_sources", summary, details, markdown)


if __name__ == "__main__":
    asyncio.run(main())
