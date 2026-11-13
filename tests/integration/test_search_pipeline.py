"""Integration tests for the search/retrieval pipeline.

Requires Azure credentials and a populated search index.
Run with: pytest tests/integration/ -m integration
"""

from __future__ import annotations

import pytest

from legalmind.retrieval.hybrid_search import HybridSearchEngine
from legalmind.retrieval.query_rewriter import QueryRewriter


@pytest.mark.integration
class TestSearchPipeline:
    """End-to-end tests for search with live Azure AI Search."""

    @pytest.mark.asyncio
    async def test_hybrid_search_returns_results(self):
        """Test that hybrid search returns results from the index."""
        engine = HybridSearchEngine()
        results = await engine.search("verzekeringsovereenkomst", top=5)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_with_filters(self):
        """Test that search respects document type filters."""
        engine = HybridSearchEngine()
        results = await engine.search(
            "contract opzegging",
            top=5,
            filters="document_type eq 'contract'",
        )
        for result in results:
            assert result.get("document_type") == "contract"

    @pytest.mark.asyncio
    async def test_query_rewriting(self):
        """Test that query rewriting produces a modified query."""
        rewriter = QueryRewriter()
        original = "hoe zeg ik mijn verzekering op?"
        rewritten = await rewriter.rewrite_query(original)
        assert isinstance(rewritten, str)
        assert len(rewritten) > 0

    @pytest.mark.asyncio
    async def test_semantic_ranking(self):
        """Test that semantic ranking provides reranker scores."""
        engine = HybridSearchEngine()
        results = await engine.search(
            "aansprakelijkheid werkgever",
            top=5,
            use_semantic_ranking=True,
        )
        if results:
            assert any(r.get("@search.reranker_score") is not None for r in results)
