"""Semantic Kernel plugin wrapping hybrid search from legalmind.retrieval.

Provides legal document search combining vector similarity (embeddings) and
keyword matching (BM25) via Azure AI Search. Used by the Document Analysis,
Research, and Compliance agents to find relevant legal content.
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.config import get_settings
from legalmind.retrieval.hybrid_search import HybridSearchEngine, SearchFilters

logger = structlog.get_logger(__name__)


class SearchTool:
    """Semantic Kernel plugin for hybrid legal document search.

    Wraps the HybridSearchEngine to provide vector + keyword search over
    the Azure AI Search index of Dutch legal documents.
    """

    def __init__(self, search_engine: HybridSearchEngine | None = None) -> None:
        """Initialize the search tool.

        Args:
            search_engine: An existing HybridSearchEngine instance. If not
                provided, a new one is created using default configuration.
        """
        self._engine = search_engine or HybridSearchEngine()

    @kernel_function(
        name="search_legal_documents",
        description=(
            "Search the Dutch legal knowledge base for relevant documents, case law, "
            "statutes, and legal references. Returns ranked results with content snippets "
            "and relevance scores. Use this when you need to find legal information, "
            "precedents, or document sections relevant to a query."
        ),
    )
    async def search_legal_documents(
        self,
        query: Annotated[str, "The search query describing what legal information to find"],
        top_k: Annotated[int, "Maximum number of results to return (default: 5)"] = 5,
        document_type: Annotated[
            str,
            "Optional filter by document type: 'case_law', 'statute', 'contract', "
            "'correspondence', 'policy', or 'all' (default: 'all')",
        ] = "all",
    ) -> Annotated[str, "JSON array of search results with title, content, score, and metadata"]:
        """Search the legal knowledge base using hybrid search.

        Combines vector similarity search (for semantic meaning) with keyword
        search (for exact legal terms and references) to find the most relevant
        legal documents.

        Args:
            query: The search query.
            top_k: Maximum number of results to return.
            document_type: Optional filter by document type.

        Returns:
            JSON string containing an array of search results.
        """
        logger.info(
            "search_tool_invoked",
            query=query[:100],
            top_k=top_k,
            document_type=document_type,
        )

        if not get_settings().search.endpoint:
            # Local development without Azure AI Search. Tell the model not to retry.
            logger.warning("search_tool_skipped", reason="AZURE_SEARCH_ENDPOINT not set")
            return json.dumps(
                {
                    "error": "The document knowledge base is not available. Do not search again; "
                    "answer from general legal knowledge and say no documents were consulted.",
                    "results": [],
                }
            )

        filters = None
        if document_type and document_type != "all":
            filters = SearchFilters(document_types=[document_type])

        try:
            results = await self._engine.hybrid_search(query, filters=filters, top=top_k)

            formatted_results = []
            for result in results:
                formatted_results.append(
                    {
                        "title": result.title,
                        "content": result.content,
                        "score": round(result.reranker_score or result.search_score, 4),
                        "document_type": result.document_type or "unknown",
                        "section": result.section_title or "",
                        "page": result.page_number,
                        "source": result.source_url or "",
                        "date": result.created_at or "",
                        "chunk_id": result.id,
                    }
                )

            logger.info("search_tool_results", result_count=len(formatted_results))
            return json.dumps(formatted_results, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("search_tool_error", query=query[:100])
            return json.dumps(
                {
                    "error": "Search failed. Please try rephrasing your query.",
                    "results": [],
                }
            )

    @kernel_function(
        name="search_case_law",
        description=(
            "Search specifically for Dutch case law (jurisprudentie) by ECLI number, "
            "court name, legal topic, or keywords from court decisions."
        ),
    )
    async def search_case_law(
        self,
        query: Annotated[str, "Search query for case law — can include ECLI numbers, court names, or legal topics"],
        top_k: Annotated[int, "Maximum number of results to return (default: 5)"] = 5,
    ) -> Annotated[str, "JSON array of case law search results"]:
        """Search specifically for Dutch case law.

        Args:
            query: The case law search query.
            top_k: Maximum number of results.

        Returns:
            JSON string containing case law search results.
        """
        logger.info("search_case_law_invoked", query=query[:100], top_k=top_k)

        result: str = await self.search_legal_documents(
            query=query,
            top_k=top_k,
            document_type="case_law",
        )
        return result

    @kernel_function(
        name="search_statutes",
        description=(
            "Search for Dutch and EU statutes, regulations, and legislative provisions. "
            "Use this when looking up specific articles of law (e.g., Burgerlijk Wetboek, AVG)."
        ),
    )
    async def search_statutes(
        self,
        query: Annotated[str, "Search query for statutes — can include article numbers, law names, or legal topics"],
        top_k: Annotated[int, "Maximum number of results to return (default: 5)"] = 5,
    ) -> Annotated[str, "JSON array of statute search results"]:
        """Search specifically for statutes and regulations.

        Args:
            query: The statute search query.
            top_k: Maximum number of results.

        Returns:
            JSON string containing statute search results.
        """
        logger.info("search_statutes_invoked", query=query[:100], top_k=top_k)

        result: str = await self.search_legal_documents(
            query=query,
            top_k=top_k,
            document_type="statute",
        )
        return result
