"""Search keeps working when the semantic ranker's quota runs out (it took dev down on 21 Nov 2026)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.exceptions import HttpResponseError

from legalmind.access import Principal
from legalmind.retrieval import hybrid_search
from legalmind.retrieval.hybrid_search import HybridSearchEngine

QUOTA = "() Free Query Semantic Usage exceeded for the month. Please enable semantic billing to continue"


class QuotaSearch:
    """Refuses semantic queries like AI Search does once the free quota is used up."""

    def __init__(self, error: str = QUOTA) -> None:
        self.error = error
        self.query_types: list[str] = []

    async def hybrid_search(self, query: str, *, query_type: str = "simple", **_: Any) -> list[dict[str, Any]]:
        self.query_types.append(query_type)
        if query_type == "semantic":
            raise HttpResponseError(message=self.error)
        return [{"id": "c1", "document_id": "d1", "title": "Huurovereenkomst", "content": "...", "@search.score": 0.03}]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> tuple[HybridSearchEngine, QuotaSearch]:
    monkeypatch.setattr(hybrid_search, "_semantic_unavailable_until", 0.0)
    fake = QuotaSearch()
    openai = MagicMock()
    openai.generate_single_embedding = AsyncMock(return_value=[0.0] * 8)
    monkeypatch.setattr(hybrid_search, "SearchService", lambda: fake)
    monkeypatch.setattr(hybrid_search, "AzureOpenAIService", lambda: openai)
    return HybridSearchEngine(), fake


async def test_search_falls_back_to_plain_hybrid(engine: tuple[HybridSearchEngine, QuotaSearch]) -> None:
    search, fake = engine
    results = await search.hybrid_search("huurprijs", access=Principal.everything())
    assert [r.id for r in results] == ["c1"]
    assert fake.query_types == ["semantic", "simple"]


async def test_later_searches_skip_the_semantic_ranker_for_a_while(
    engine: tuple[HybridSearchEngine, QuotaSearch],
) -> None:
    search, fake = engine
    await search.hybrid_search("huurprijs", access=Principal.everything())
    await search.hybrid_search("opzegtermijn", access=Principal.everything())
    assert fake.query_types == ["semantic", "simple", "simple"]  # no second failed attempt


async def test_other_search_errors_still_fail(
    engine: tuple[HybridSearchEngine, QuotaSearch],
) -> None:
    search, fake = engine
    fake.error = "() Forbidden"  # e.g. the app identity lost its role: an incident, not a fallback
    with pytest.raises(HttpResponseError, match="Forbidden"):
        await search.hybrid_search("huurprijs", access=Principal.everything())
