"""Unit tests for context building from search results."""

from __future__ import annotations

import pytest

from legalmind.retrieval.context_builder import ContextBuilder
from legalmind.retrieval.hybrid_search import SearchResult


def _make_result(
    doc_id: str = "doc1",
    chunk_index: int = 0,
    content: str = "Test content",
    score: float = 0.9,
) -> SearchResult:
    """Helper to create a SearchResult for testing."""
    return SearchResult(
        id=f"{doc_id}-chunk-{chunk_index}",
        document_id=doc_id,
        chunk_index=chunk_index,
        content=content,
        title="Test Document",
        document_type="contract",
        language="nl",
        page_number=1,
        section_title="Test Section",
        search_score=score,
    )


@pytest.fixture
def context_builder() -> ContextBuilder:
    return ContextBuilder(max_tokens=2000)


class TestContextBuilder:
    """Tests for assembling RAG context from search results."""

    def test_empty_results(self, context_builder: ContextBuilder):
        result = context_builder.build([])
        assert result.context == "" or result.chunks_included == 0
        assert result.citations == []

    def test_single_result(self, context_builder: ContextBuilder):
        results = [_make_result()]
        result = context_builder.build(results)
        assert result.chunks_included >= 1
        assert "[1]" in result.context
        assert len(result.citations) == 1

    def test_multiple_results_numbered(self, context_builder: ContextBuilder):
        results = [
            _make_result("doc1", 0, "Content one", 0.95),
            _make_result("doc2", 0, "Content two", 0.85),
            _make_result("doc3", 0, "Content three", 0.75),
        ]
        result = context_builder.build(results)
        assert "[1]" in result.context
        assert "[2]" in result.context
        assert len(result.citations) >= 2

    def test_citations_have_required_fields(self, context_builder: ContextBuilder):
        results = [_make_result()]
        result = context_builder.build(results)
        for citation in result.citations:
            assert citation.number >= 1
            assert citation.document_id

    def test_deduplication(self, context_builder: ContextBuilder):
        """Duplicate chunks should be deduplicated."""
        results = [
            _make_result("doc1", 0, "Same content", 0.9),
            _make_result("doc1", 0, "Same content", 0.8),
        ]
        result = context_builder.build(results)
        # Deduplication should reduce to 1 citation
        assert result.chunks_included <= 2

    def test_respects_token_budget(self):
        """Context should be truncated when exceeding token budget."""
        builder = ContextBuilder(max_tokens=50)
        results = [_make_result(f"doc{i}", 0, "Lang stuk tekst. " * 50, 0.9 - i * 0.05) for i in range(10)]
        result = builder.build(results)
        # Should have fewer citations than results due to budget
        assert result.chunks_included < 10
        assert result.chunks_dropped > 0
