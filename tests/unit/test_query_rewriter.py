"""Unit tests for LLM-based query rewriting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from legalmind.retrieval.query_rewriter import QueryRewriter


@pytest.fixture
def mock_openai():
    with patch("legalmind.retrieval.query_rewriter.AzureOpenAIService") as mock:
        service = AsyncMock()
        service.chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": "Rewritten query about legal termination conditions"}}],
            }
        )
        service.generate_single_embedding = AsyncMock(return_value=[0.1] * 3072)
        mock.return_value = service
        yield service


class TestQueryRewriter:
    """Tests for query rewriting strategies."""

    @pytest.mark.asyncio
    async def test_rewrite_returns_result(self, mock_openai):
        rewriter = QueryRewriter(openai_service=mock_openai)
        result = await rewriter.rewrite_query("Wat zijn de opzegvoorwaarden?")
        # Returns a RewrittenQuery dataclass with .rewritten attribute
        assert hasattr(result, "rewritten") or isinstance(result, str)
        rewritten = result.rewritten if hasattr(result, "rewritten") else result
        assert len(rewritten) > 0

    @pytest.mark.asyncio
    async def test_decompose_returns_sub_queries(self, mock_openai):
        mock_openai.chat_completion.return_value = {
            "choices": [{"message": {"content": '["First sub-query", "Second sub-query"]'}}],
        }
        rewriter = QueryRewriter(openai_service=mock_openai)
        result = await rewriter.decompose_query(
            "Vergelijk de opzegtermijnen in contract A en B en geef aan welke gunstiger is"
        )
        # Returns a DecomposedQuery with .sub_queries
        sub_queries = result.sub_queries if hasattr(result, "sub_queries") else result
        assert isinstance(sub_queries, list)
        assert len(sub_queries) >= 1

    @pytest.mark.asyncio
    async def test_hyde_generates_hypothetical_doc(self, mock_openai):
        mock_openai.chat_completion.return_value = {
            "choices": [{"message": {"content": "Hypothetical answer about termination clauses..."}}],
        }
        rewriter = QueryRewriter(openai_service=mock_openai)
        result = await rewriter.generate_hyde("Opzegtermijn contract")
        # Returns a HydeResult with .hypothetical_passage
        passage = result.hypothetical_passage if hasattr(result, "hypothetical_passage") else result
        assert isinstance(passage, str)
        assert len(passage) > 0

    @pytest.mark.asyncio
    async def test_rewrite_calls_openai(self, mock_openai):
        rewriter = QueryRewriter(openai_service=mock_openai)
        await rewriter.rewrite_query("Test query")
        mock_openai.chat_completion.assert_called_once()
