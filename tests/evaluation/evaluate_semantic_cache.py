"""The AI gateway's semantic cache: hit rate, whether cached answers stay right, and access separation.

Against a deployed API whose gateway has the semantic cache on (``SEMANTIC_CACHE=true``):

1. **fill**: N answerable golden-set questions as a lawyer on every matter (cache misses)
2. **repeat**: the same questions, same user: model calls can come from the cache
3. **other access**: the same questions as a paralegal on one matter (another ``x-legalmind-access``
   partition): nothing may come from the lawyer's entries

Per pass, the gateway's model calls (``requests`` of API Management) are compared with the calls it
forwarded to Azure OpenAI (``dependencies``): the difference is what the cache answered. Answers are
checked like the generation evaluation (facts present, the app's grounding label).

    API_URL=https://<api> python -m tests.evaluation.evaluate_semantic_cache [--questions 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import ssl
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import truststore

from tests.evaluation._common import load_dataset, markdown_table, mean, normalise, write_report

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
RESOURCE_GROUP = os.getenv("RESOURCE_GROUP", "rg-legalmind-dev")
APP_INSIGHTS = os.getenv("APP_INSIGHTS", "appi-legalmind-dev")
MATTERS = "BM/2026/A-0377,BM/2026/H-0412,BM/2026/V-0198,VRB/2024/ZK-9012"
PASSES = {
    "fill": {"X-Dev-User": "cache-lawyer", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": MATTERS},
    "repeat": {"X-Dev-User": "cache-lawyer", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": MATTERS},
    "other access": {"X-Dev-User": "cache-paralegal", "X-Dev-Roles": "Paralegal", "X-Dev-Matters": "BM/2026/H-0412"},
}
INGESTION_DELAY_S = 240  # Application Insights needs a few minutes before the calls can be counted


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _facts(answer: str, facts: list[list[str]]) -> float:
    text = normalise(answer)
    found = sum(any(normalise(v) in text for v in variants) for variants in facts)
    return found / len(facts) if facts else 1.0


def _gateway_calls(start: str, end: str) -> dict[str, int]:
    """Chat-completion calls at the gateway vs. those forwarded to a model backend, in a window."""
    query = (
        'let chat = (n: string) { n has "chat/completions" or n has "chat-completions" };'
        ' let gw = toscalar(requests | where cloud_RoleName has "apim" and chat(name) | count);'
        ' let fwd = toscalar(dependencies | where cloud_RoleName has "apim" and target has "openai"'
        ' and not(target has "embeddings") and chat(name) | count);'
        " print gateway = gw, forwarded = fwd"
    )
    az = shutil.which("az") or "az"
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [az, "monitor", "app-insights", "query", "-g", RESOURCE_GROUP, "--app", APP_INSIGHTS,
         "--start-time", start, "--end-time", end, "--analytics-query", query, "-o", "json"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    row = json.loads(out.stdout)["tables"][0]["rows"][0]
    return {"gateway": int(row[0]), "forwarded": int(row[1])}


async def _ask(client: httpx.AsyncClient, question: str, headers: dict[str, str]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = await client.post(f"{API_URL}/api/chat", json={"message": question, "stream": False}, headers=headers)
    response.raise_for_status()
    return response.json(), time.perf_counter() - started


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=int, default=20)
    args = parser.parse_args()
    queries = [q for q in load_dataset("generation_golden_set.json") if q["answerable"]][: args.questions]

    windows: dict[str, tuple[str, str]] = {}
    answers: dict[str, dict[str, dict[str, Any]]] = {}
    verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    async with httpx.AsyncClient(timeout=240, verify=verify) as client:
        for name, headers in PASSES.items():
            start = _now()
            answers[name] = {}
            for q in queries:
                reply, latency = await _ask(client, q["question"], headers)
                answers[name][q["id"]] = {
                    "facts": _facts(reply["message"], q["must_contain"]),
                    "grounding": reply.get("grounding", ""),
                    "latency": latency,
                    "message": reply["message"],
                }
                print(f"{name} {q['id']}: {latency:.1f}s {reply.get('grounding')}", flush=True)  # noqa: T201
            windows[name] = (start, _now())

    print(f"waiting {INGESTION_DELAY_S}s for Application Insights", flush=True)  # noqa: T201
    await asyncio.sleep(INGESTION_DELAY_S)

    summary_rows = []
    for name, (start, end) in windows.items():
        calls = _gateway_calls(start, end)
        hits = max(0, calls["gateway"] - calls["forwarded"])
        replies = answers[name].values()
        summary_rows.append(
            {
                "pass": name,
                "questions": len(queries),
                "model_calls_at_gateway": calls["gateway"],
                "forwarded_to_model": calls["forwarded"],
                "cache_hit_rate": hits / calls["gateway"] if calls["gateway"] else 0.0,
                "fact_recall": mean([a["facts"] for a in replies]),
                "grounded": mean([1.0 if a["grounding"] == "documents" else 0.0 for a in replies]),
                "latency_mean_s": mean([a["latency"] for a in replies]),
            }
        )

    details = [
        {
            "id": q["id"],
            "facts_fill": answers["fill"][q["id"]]["facts"],
            "facts_repeat": answers["repeat"][q["id"]]["facts"],
            "same_answer": answers["fill"][q["id"]]["message"] == answers["repeat"][q["id"]]["message"],
            "grounding_fill": answers["fill"][q["id"]]["grounding"],
            "grounding_repeat": answers["repeat"][q["id"]]["grounding"],
            "latency_fill_s": round(answers["fill"][q["id"]]["latency"], 1),
            "latency_repeat_s": round(answers["repeat"][q["id"]]["latency"], 1),
        }
        for q in queries
    ]
    summary = {row["pass"]: row for row in summary_rows}
    markdown = (
        "# Semantic cache at the AI gateway\n\n"
        + markdown_table(summary_rows)
        + "\n## Fill vs repeat, per question\n\n"
        + markdown_table(details)
    )
    write_report("semantic_cache", summary, details, markdown)


if __name__ == "__main__":
    asyncio.run(main())
