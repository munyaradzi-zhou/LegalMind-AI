"""Direct search endpoint for querying the legal knowledge base."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from legalmind.api.models.requests import SearchRequest
from legalmind.api.models.responses import SearchResponse, SearchResult
from legalmind.retrieval.hybrid_search import HybridSearchEngine, SearchFilters
from legalmind.retrieval.query_rewriter import QueryRewriter

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search_documents(request: SearchRequest) -> SearchResponse:
    """Search the legal document knowledge base.

    Supports hybrid search (vector + keyword), semantic re-ranking,
    and LLM-powered query rewriting for optimal results.
    """
    logger.info("search_request", query=request.query[:100], top=request.top)

    search_engine = HybridSearchEngine()

    # Optionally rewrite the query
    query_used = request.query
    if request.use_query_rewriting:
        rewriter = QueryRewriter()
        query_used = (await rewriter.rewrite_query(request.query)).rewritten

    # Build filters
    filters = None
    if request.document_type or request.language:
        filters = SearchFilters(
            document_types=[request.document_type] if request.document_type else None,
            language=request.language,
        )

    # Execute search
    results = await search_engine.hybrid_search(
        query_used,
        filters=filters,
        top=request.top,
        enable_semantic_ranking=request.use_semantic_ranking,
    )

    # Map to response model
    search_results = [
        SearchResult(
            document_id=r.document_id,
            chunk_id=r.id,
            content=r.content,
            title=r.title,
            document_type=r.document_type,
            page_number=r.page_number,
            score=r.search_score,
            reranker_score=r.reranker_score,
            entities=r.entities,
            key_phrases=r.key_phrases,
        )
        for r in results
    ]

    return SearchResponse(
        results=search_results,
        total_count=len(search_results),
        query_used=query_used,
    )
