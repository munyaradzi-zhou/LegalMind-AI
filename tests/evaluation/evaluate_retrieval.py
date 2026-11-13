"""Retrieval quality: does the right passage come back, and how high?

Compares five retrieval modes on the golden set (datasets/retrieval_golden_set.json):

- keyword:     BM25 only
- vector:      embeddings only (text-embedding-3-large, HNSW)
- hybrid:      BM25 + vectors fused with Reciprocal Rank Fusion
- hybrid+sem:  hybrid, then the semantic (cross-encoder) re-ranker
- production:  LLM query rewriting, then hybrid+sem — what /api/chat does

Metrics per mode: hit@1, hit@3, hit@5 (a top-k chunk contains the expected passage),
MRR@10, source@1 (the top chunk comes from the right document) and latency.

Runs against the real Azure services configured in the environment:
    python -m tests.evaluation.evaluate_retrieval
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from legalmind.retrieval.hybrid_search import HybridSearchEngine
from legalmind.retrieval.query_rewriter import QueryRewriter
from legalmind.services.azure_openai import AzureOpenAIService
from tests.evaluation._common import load_dataset, markdown_table, mean, normalise, percentile, write_report

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from legalmind.retrieval.hybrid_search import SearchResult

TOP_K = 10


def _rank_of_passage(results: list[SearchResult], passage: str) -> int | None:
    """1-based rank of the first chunk containing the passage, or None."""
    needle = normalise(passage)
    for rank, result in enumerate(results, 1):
        if needle in normalise(result.content):
            return rank
    return None


async def _reachable(engine: HybridSearchEngine, queries: list[dict[str, Any]]) -> set[str]:
    """IDs of queries whose passage exists in at least one indexed chunk (sanity check)."""
    reachable = set()
    for q in queries:
        exact = await engine.keyword_search(f'"{q["passage"]}"', top=50)
        if _rank_of_passage(exact, q["passage"]) is not None:
            reachable.add(q["id"])
    return reachable


async def evaluate_retrieval() -> dict[str, Any]:
    queries = load_dataset("retrieval_golden_set.json")
    openai = AzureOpenAIService()
    engine = HybridSearchEngine(openai_service=openai)
    rewriter = QueryRewriter(openai_service=openai)

    async def production(query: str) -> list[SearchResult]:
        rewritten = await rewriter.rewrite_query(query)
        return await engine.hybrid_search(rewritten.rewritten, top=TOP_K, enable_semantic_ranking=True)

    modes: dict[str, Callable[[str], Awaitable[list[SearchResult]]]] = {
        "keyword": lambda q: engine.keyword_search(q, top=TOP_K),
        "vector": lambda q: engine.vector_search(q, top=TOP_K),
        "hybrid": lambda q: engine.hybrid_search(q, top=TOP_K, enable_semantic_ranking=False),
        "hybrid+sem": lambda q: engine.hybrid_search(q, top=TOP_K, enable_semantic_ranking=True),
        "production": production,
    }

    reachable = await _reachable(engine, queries)
    details: list[dict[str, Any]] = []
    for q in queries:
        row: dict[str, Any] = {
            "id": q["id"],
            "query": q["query"],
            "language": q["language"],
            "category": q["category"],
            "reachable": q["id"] in reachable,
        }
        for mode, search in modes.items():
            started = time.perf_counter()
            results = await search(q["query"])
            row[f"{mode}_ms"] = round((time.perf_counter() - started) * 1000)
            row[f"{mode}_rank"] = _rank_of_passage(results, q["passage"])
            row[f"{mode}_source1"] = bool(results) and results[0].title == q["source"]
        details.append(row)

    def stats(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
        ranks = [r[f"{mode}_rank"] for r in rows]
        return {
            "mode": mode,
            "hit@1": mean([1.0 if rk == 1 else 0.0 for rk in ranks]),
            "hit@3": mean([1.0 if rk and rk <= 3 else 0.0 for rk in ranks]),
            "hit@5": mean([1.0 if rk and rk <= 5 else 0.0 for rk in ranks]),
            "mrr@10": mean([1.0 / rk if rk else 0.0 for rk in ranks]),
            "source@1": mean([1.0 if r[f"{mode}_source1"] else 0.0 for r in rows]),
            "p50_ms": percentile([r[f"{mode}_ms"] for r in rows], 50),
            "p95_ms": percentile([r[f"{mode}_ms"] for r in rows], 95),
        }

    overall = [stats(details, m) for m in modes]
    by_language = []
    for lang in ("nl", "en"):
        subset = [r for r in details if r["language"] == lang]
        for m in ("hybrid+sem", "production"):
            by_language.append({"language": lang, "queries": len(subset), **stats(subset, m)})
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in details:
        by_category[r["category"]].append(r)
    category_rows = [
        {"category": cat, "queries": len(rows), "hit@3 (production)": stats(rows, "production")["hit@3"]}
        for cat, rows in sorted(by_category.items())
    ]
    misses = [
        {
            "id": r["id"],
            "query": r["query"],
            "production rank": r["production_rank"],
            "hybrid+sem rank": r["hybrid+sem_rank"],
        }
        for r in details
        if not r["production_rank"] or r["production_rank"] > 3
    ]

    summary = {"queries": len(queries), "reachable": len(reachable), "top_k": TOP_K, "modes": overall}
    md = (
        "# Retrieval evaluation\n\n"
        f"{len(queries)} queries on the sample corpus; {len(reachable)} expected passages found in the index "
        "(a passage split across two chunks can't be found by any mode).\n\n"
        "## By retrieval mode\n\n" + markdown_table(overall) + "\n"
        "## Dutch vs English questions (corpus is Dutch)\n\n" + markdown_table(by_language) + "\n"
        "## By category\n\n" + markdown_table(category_rows) + "\n"
        "## Production misses (passage not in the top 3)\n\n" + markdown_table(misses)
    )
    write_report("retrieval", summary, details, md)
    return summary


if __name__ == "__main__":
    asyncio.run(evaluate_retrieval())
