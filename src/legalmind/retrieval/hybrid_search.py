"""Hybrid vector + keyword search engine for Dutch legal documents.

Combines dense vector similarity (text-embedding-3-large) with BM25 keyword
matching via Azure AI Search's built-in Reciprocal Rank Fusion (RRF) and
optional semantic re-ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from legalmind.config import get_settings
from legalmind.services import AzureOpenAIService, SearchService

if TYPE_CHECKING:
    from datetime import datetime

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class DocumentType(StrEnum):
    """Allowed legal document types for filtering."""

    CONTRACT = "contract"
    CORRESPONDENCE = "correspondence"
    CASE_FILE = "case_file"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """OData filter parameters for narrowing search results.

    All fields are optional; only non-``None`` values are included in the
    generated OData expression.
    """

    document_types: list[DocumentType | str] | None = None
    language: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    parent_id: str | None = None
    document_ids: list[str] | None = None

    def to_odata(self) -> str | None:
        """Compile filter fields into an OData $filter expression.

        Returns:
            An OData expression string, or ``None`` if no filters are set.
        """
        clauses: list[str] = []

        if self.document_types:
            values = " or ".join(f"document_type eq '{_escape(str(dt))}'" for dt in self.document_types)
            clauses.append(f"({values})")

        if self.language:
            clauses.append(f"language eq '{_escape(self.language)}'")

        if self.date_from:
            clauses.append(f"created_at ge {self.date_from.isoformat()}Z")

        if self.date_to:
            clauses.append(f"created_at le {self.date_to.isoformat()}Z")

        if self.parent_id:
            clauses.append(f"parent_id eq '{_escape(self.parent_id)}'")

        if self.document_ids:
            values = " or ".join(f"document_id eq '{_escape(d)}'" for d in self.document_ids)
            clauses.append(f"({values})")

        return " and ".join(clauses) if clauses else None


def _escape(value: str) -> str:
    """Escape a value for an OData string literal (a single quote is written as two)."""
    return value.replace("'", "''")


@dataclass(slots=True)
class SearchResult:
    """A single search result with content, metadata, and relevance scores."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    title: str
    document_type: str
    language: str
    page_number: int | None
    section_title: str | None
    entities: list[str] = field(default_factory=list)
    key_phrases: list[str] = field(default_factory=list)
    summary: str | None = None
    parent_id: str | None = None
    source_url: str | None = None
    created_at: str | None = None

    # Relevance scores
    search_score: float = 0.0
    reranker_score: float | None = None

    @classmethod
    def from_raw(cls, doc: dict[str, Any]) -> SearchResult:
        """Construct a ``SearchResult`` from a raw Azure Search result dict.

        Args:
            doc: A document dictionary returned by the Azure Search SDK.

        Returns:
            A typed ``SearchResult`` instance.
        """
        return cls(
            id=doc.get("id", ""),
            document_id=doc.get("document_id", ""),
            chunk_index=doc.get("chunk_index", 0),
            content=doc.get("content", ""),
            title=doc.get("title", ""),
            document_type=doc.get("document_type", ""),
            language=doc.get("language", ""),
            page_number=doc.get("page_number"),
            section_title=doc.get("section_title"),
            entities=doc.get("entities") or [],
            key_phrases=doc.get("key_phrases") or [],
            summary=doc.get("summary"),
            parent_id=doc.get("parent_id"),
            source_url=doc.get("source_url"),
            created_at=doc.get("created_at"),
            search_score=doc.get("@search.score", 0.0),
            reranker_score=doc.get("@search.reranker_score"),
        )


# ---------------------------------------------------------------------------
# Default projection — fields to retrieve from the index
# ---------------------------------------------------------------------------

_DEFAULT_SELECT: list[str] = [
    "id",
    "document_id",
    "chunk_index",
    "content",
    "title",
    "document_type",
    "language",
    "page_number",
    "section_title",
    "entities",
    "key_phrases",
    "summary",
    "parent_id",
    "source_url",
    "created_at",
]


# ---------------------------------------------------------------------------
# Hybrid search engine
# ---------------------------------------------------------------------------


class HybridSearchEngine:
    """Orchestrates hybrid (vector + keyword + semantic) retrieval.

    This class ties together the ``AzureOpenAIService`` (for query embedding)
    and ``SearchService`` (for index querying) to provide a single
    ``hybrid_search`` entry point that:

    1. Generates a query embedding via text-embedding-3-large.
    2. Sends a hybrid request to Azure AI Search which internally performs
       Reciprocal Rank Fusion (RRF) across the BM25 keyword ranking and the
       HNSW vector ranking.
    3. Optionally applies the semantic cross-encoder re-ranker.
    4. Converts raw results into typed ``SearchResult`` objects.
    """

    def __init__(
        self,
        *,
        openai_service: AzureOpenAIService | None = None,
        search_service: SearchService | None = None,
    ) -> None:
        self._openai = openai_service or AzureOpenAIService()
        self._search = search_service or SearchService()
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top: int = 10,
        enable_semantic_ranking: bool = True,
        select: list[str] | None = None,
    ) -> list[SearchResult]:
        """Execute a hybrid search combining vector similarity and keyword matching.

        The search leverages Azure AI Search's built-in Reciprocal Rank Fusion
        to merge BM25 keyword scores with HNSW vector similarity scores.  When
        ``enable_semantic_ranking`` is ``True``, the results are further
        re-ranked by the semantic cross-encoder model.

        Args:
            query: The natural-language search query.
            filters: Optional structured filters (document type, date range,
                language, parent document).
            top: Maximum number of results to return.
            enable_semantic_ranking: Whether to apply semantic re-ranking on
                top of hybrid fusion.  Defaults to ``True``.
            select: Fields to project.  Defaults to all metadata fields
                (excluding the vector).

        Returns:
            A list of ``SearchResult`` objects ordered by relevance.
        """
        logger.info(
            "hybrid_search_start",
            query=query[:120],
            top=top,
            semantic=enable_semantic_ranking,
            has_filters=filters is not None,
        )

        # 1. Generate the query embedding
        query_vector = await self._openai.generate_single_embedding(query)

        # 2. Build OData filter string
        odata_filter = filters.to_odata() if filters else None

        # 3. Determine semantic configuration
        semantic_config = "legal-semantic-config" if enable_semantic_ranking else None
        query_type = "semantic" if enable_semantic_ranking else "simple"

        # 4. Execute hybrid search via SearchService
        raw_results = await self._search.hybrid_search(
            query,
            query_vector=query_vector,
            filters=odata_filter,
            top=top,
            select=select or _DEFAULT_SELECT,
            semantic_configuration=semantic_config,
            query_type=query_type,
        )

        # 5. Convert to typed results
        results = [SearchResult.from_raw(doc) for doc in raw_results]

        # 6. Sort by reranker score when available, otherwise by search score
        results.sort(
            key=lambda r: (r.reranker_score or 0.0, r.search_score),
            reverse=True,
        )

        logger.info(
            "hybrid_search_complete",
            result_count=len(results),
            top_score=results[0].search_score if results else 0.0,
        )

        return results

    async def keyword_search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top: int = 10,
    ) -> list[SearchResult]:
        """Execute a keyword-only (BM25) search without vector similarity.

        Useful for exact phrase matching or when the query contains very
        specific legal identifiers (e.g., case numbers, statute references).

        Args:
            query: The keyword search query.
            filters: Optional structured filters.
            top: Maximum number of results.

        Returns:
            Keyword-ranked ``SearchResult`` list.
        """
        logger.info("keyword_search", query=query[:120], top=top)

        odata_filter = filters.to_odata() if filters else None

        raw_results = await self._search.hybrid_search(
            query,
            query_vector=None,
            filters=odata_filter,
            top=top,
            select=_DEFAULT_SELECT,
            semantic_configuration=None,
            query_type="simple",
        )

        results = [SearchResult.from_raw(doc) for doc in raw_results]
        results.sort(key=lambda r: r.search_score, reverse=True)
        return results

    async def vector_search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top: int = 10,
    ) -> list[SearchResult]:
        """Execute a pure vector similarity search.

        Useful when the query is conceptual rather than keyword-oriented.

        Args:
            query: The natural-language query to embed and search.
            filters: Optional structured filters.
            top: Maximum number of results.

        Returns:
            Vector-similarity-ranked ``SearchResult`` list.
        """
        logger.info("vector_search", query=query[:120], top=top)

        query_vector = await self._openai.generate_single_embedding(query)
        odata_filter = filters.to_odata() if filters else None

        raw_results = await self._search.vector_search(
            query_vector,
            top=top,
            filters=odata_filter,
            select=_DEFAULT_SELECT,
        )

        results = [SearchResult.from_raw(doc) for doc in raw_results]
        return results

    async def multi_query_search(
        self,
        queries: list[str],
        *,
        filters: SearchFilters | None = None,
        top_per_query: int = 5,
        final_top: int = 10,
        enable_semantic_ranking: bool = True,
    ) -> list[SearchResult]:
        """Run multiple queries and merge results via score-based deduplication.

        Designed for use with ``QueryRewriter.decompose_query`` — each
        sub-query targets a different facet of a complex legal question.
        Results are deduplicated by chunk id and the highest score is kept.

        Args:
            queries: List of search queries.
            filters: Shared filters applied to every query.
            top_per_query: Results to fetch per individual query.
            final_top: Total results to return after merging.
            enable_semantic_ranking: Whether to apply semantic re-ranking.

        Returns:
            Deduplicated, relevance-sorted ``SearchResult`` list.
        """
        logger.info("multi_query_search", query_count=len(queries), final_top=final_top)

        seen: dict[str, SearchResult] = {}

        for query in queries:
            results = await self.hybrid_search(
                query,
                filters=filters,
                top=top_per_query,
                enable_semantic_ranking=enable_semantic_ranking,
            )
            for result in results:
                existing = seen.get(result.id)
                if existing is None:
                    seen[result.id] = result
                else:
                    # Keep the result with the higher effective score
                    new_score = result.reranker_score or result.search_score
                    old_score = existing.reranker_score or existing.search_score
                    if new_score > old_score:
                        seen[result.id] = result

        merged = list(seen.values())
        merged.sort(
            key=lambda r: (r.reranker_score or 0.0, r.search_score),
            reverse=True,
        )

        return merged[:final_top]

    # ------------------------------------------------------------------
    # Multi-hop search
    # ------------------------------------------------------------------

    # Patterns that indicate cross-references to other documents/articles
    _REFERENCE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"zie\s+artikel\s+([\d]+(?:\s*[a-z])?)", re.IGNORECASE),
        re.compile(r"conform\s+(?:artikel|art\.?)\s+([\d]+(?:\s*[a-z])?)", re.IGNORECASE),
        re.compile(r"artikel\s+([\d]+(?:\s*[a-z])?)\s+(?:van|BW|WvK|Rv)", re.IGNORECASE),
        re.compile(r"(ECLI:[A-Z]{2}:[A-Z]+:\d{4}:[A-Z0-9.]+)", re.IGNORECASE),
        re.compile(r"conform\s+contract\s+([A-Za-z0-9\-]+)", re.IGNORECASE),
        re.compile(r"op\s+grond\s+van\s+(?:artikel|art\.?)\s+([\d]+)", re.IGNORECASE),
        re.compile(r"(?:zie|vgl\.?|vergelijk)\s+(.+?)(?:\.|,|;|$)", re.IGNORECASE),
    ]

    async def multi_hop_search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top: int = 10,
        secondary_top: int = 5,
        enable_semantic_ranking: bool = True,
        max_follow_up_queries: int = 5,
    ) -> list[SearchResult]:
        """Perform a multi-hop search that follows cross-references.

        The method proceeds in three phases:

        1. **Initial search** — standard hybrid search for the user query.
        2. **Reference extraction** — scan the top results for patterns
           that cite other documents or articles (e.g., "zie artikel X",
           "conform contract Y", ECLI identifiers).
        3. **Follow-up searches** — generate targeted queries for each
           discovered reference and execute secondary searches.

        All results are merged and deduplicated, keeping the highest score
        per unique chunk ID.

        Args:
            query: The user's natural-language search query.
            filters: Optional structured filters applied to every search.
            top: Maximum results for the initial search.
            secondary_top: Maximum results per follow-up query.
            enable_semantic_ranking: Whether to use semantic re-ranking.
            max_follow_up_queries: Cap on follow-up queries to avoid
                excessive search calls.

        Returns:
            A merged, deduplicated, relevance-sorted list of
            ``SearchResult`` objects.
        """
        logger.info(
            "multi_hop_search_start",
            query=query[:120],
            top=top,
            max_follow_ups=max_follow_up_queries,
        )

        # Phase 1: initial search
        initial_results = await self.hybrid_search(
            query,
            filters=filters,
            top=top,
            enable_semantic_ranking=enable_semantic_ranking,
        )

        # Phase 2: extract cross-references from top results
        follow_up_queries = self._extract_references(initial_results[:top])

        # Deduplicate and cap
        seen_queries: set[str] = set()
        unique_queries: list[str] = []
        for fq in follow_up_queries:
            normalised = fq.strip().lower()
            if normalised not in seen_queries:
                seen_queries.add(normalised)
                unique_queries.append(fq)
            if len(unique_queries) >= max_follow_up_queries:
                break

        logger.info(
            "multi_hop_follow_up_queries",
            count=len(unique_queries),
            queries=[q[:80] for q in unique_queries],
        )

        # Phase 3: follow-up searches
        all_results: dict[str, SearchResult] = {r.id: r for r in initial_results}

        for follow_query in unique_queries:
            try:
                secondary = await self.hybrid_search(
                    follow_query,
                    filters=filters,
                    top=secondary_top,
                    enable_semantic_ranking=enable_semantic_ranking,
                )
            except Exception:
                logger.exception(
                    "multi_hop_secondary_search_error",
                    follow_query=follow_query[:120],
                )
                continue

            for result in secondary:
                existing = all_results.get(result.id)
                if existing is None:
                    all_results[result.id] = result
                else:
                    new_score = result.reranker_score or result.search_score
                    old_score = existing.reranker_score or existing.search_score
                    if new_score > old_score:
                        all_results[result.id] = result

        # Merge, sort, return
        merged = list(all_results.values())
        merged.sort(
            key=lambda r: (r.reranker_score or 0.0, r.search_score),
            reverse=True,
        )

        logger.info(
            "multi_hop_search_complete",
            initial_count=len(initial_results),
            follow_up_count=len(unique_queries),
            merged_count=len(merged),
        )

        return merged

    # ------------------------------------------------------------------
    # Helpers for multi-hop
    # ------------------------------------------------------------------

    @classmethod
    def _extract_references(cls, results: list[SearchResult]) -> list[str]:
        """Extract cross-reference queries from search result content.

        Scans each result's content for patterns like "zie artikel X",
        "conform contract Y", or ECLI identifiers, and converts each
        match into a follow-up search query.

        Args:
            results: The search results to scan.

        Returns:
            A list of follow-up query strings.
        """
        queries: list[str] = []

        for result in results:
            content = result.content
            for pattern in cls._REFERENCE_PATTERNS:
                for match in pattern.finditer(content):
                    ref_text = match.group(1).strip() if match.lastindex else match.group(0).strip()
                    if len(ref_text) < 3:
                        continue

                    # Build a contextual query from the reference
                    if ref_text.upper().startswith("ECLI:"):
                        queries.append(ref_text)
                    elif ref_text.isdigit() or re.match(r"^\d+\s*[a-z]?$", ref_text):
                        queries.append(f"artikel {ref_text}")
                    else:
                        queries.append(ref_text)

        return queries
