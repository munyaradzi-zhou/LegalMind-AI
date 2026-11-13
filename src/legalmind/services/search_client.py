"""Azure AI Search service client for indexing and querying."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.models import VectorizedQuery
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_azure_credential, get_settings

if TYPE_CHECKING:
    from azure.search.documents.indexes.models import SearchIndex

logger = structlog.get_logger(__name__)


class SearchService:
    """Wrapper around Azure AI Search for indexing and hybrid search.

    Supports:
    - Vector search (embedding similarity)
    - Keyword search (BM25)
    - Hybrid search (vector + keyword with RRF fusion)
    - Semantic ranking (cross-encoder re-ranking)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_azure_credential()  # sync index management client
        self._async_credential = get_async_azure_credential()  # async query client
        self._endpoint = settings.search.endpoint
        self._index_name = settings.search.index_name

    def _get_index_client(self) -> SearchIndexClient:
        """Get a synchronous index management client."""
        return SearchIndexClient(
            endpoint=self._endpoint,
            credential=self._credential,
        )

    def _get_search_client(self) -> AsyncSearchClient:
        """Get an async search client for querying."""
        return AsyncSearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=self._async_credential,
        )

    def create_or_update_index(self, index: SearchIndex) -> None:
        """Create or update a search index.

        Args:
            index: The SearchIndex definition to create or update.
        """
        client = self._get_index_client()
        client.create_or_update_index(index)
        logger.info("search_index_updated", index_name=index.name)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def upload_documents(self, documents: list[dict[str, Any]]) -> int:
        """Upload or merge documents into the search index.

        Args:
            documents: List of documents to index. Each must have an 'id' field.

        Returns:
            Number of documents the index accepted.
        """
        async with self._get_search_client() as client:
            result = await client.upload_documents(documents=documents)
            succeeded = sum(1 for r in result if r.succeeded)
            for failure in (r for r in result if not r.succeeded):
                logger.warning("document_upload_failed", key=failure.key, error=failure.error_message)
            logger.info("documents_uploaded", total=len(documents), succeeded=succeeded)
            return succeeded

    async def hybrid_search(
        self,
        query: str,
        *,
        query_vector: list[float] | None = None,
        filters: str | None = None,
        top: int = 10,
        select: list[str] | None = None,
        semantic_configuration: str | None = "legal-semantic-config",
        query_type: str = "semantic",
    ) -> list[dict[str, Any]]:
        """Execute a hybrid search combining vector and keyword search.

        Args:
            query: The text query for keyword search.
            query_vector: Optional pre-computed embedding vector for vector search.
            filters: OData filter expression.
            top: Number of results to return.
            select: Fields to include in results.
            semantic_configuration: Name of the semantic configuration for re-ranking.
            query_type: Search query type ('simple', 'full', 'semantic').

        Returns:
            List of search result documents with scores.
        """
        logger.info("hybrid_search", query=query[:100], top=top, filters=filters)

        search_kwargs: dict[str, Any] = {
            "search_text": query,
            "top": top,
            "query_type": query_type,
        }

        if query_vector:
            search_kwargs["vector_queries"] = [
                VectorizedQuery(
                    vector=query_vector,
                    k_nearest_neighbors=top,
                    fields="content_vector",
                )
            ]

        if filters:
            search_kwargs["filter"] = filters

        if select:
            search_kwargs["select"] = select

        if semantic_configuration and query_type == "semantic":
            search_kwargs["semantic_configuration_name"] = semantic_configuration

        results = []
        async with self._get_search_client() as client:
            async for result in await client.search(**search_kwargs):
                doc = dict(result)
                doc["@search.score"] = result.get("@search.score", 0)
                doc["@search.reranker_score"] = result.get("@search.reranker_score")
                results.append(doc)

        logger.info("hybrid_search_results", result_count=len(results))
        return results

    async def vector_search(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        filters: str | None = None,
        select: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a pure vector similarity search.

        Args:
            query_vector: The embedding vector to search with.
            top: Number of results.
            filters: OData filter expression.
            select: Fields to include.

        Returns:
            List of matching documents sorted by vector similarity.
        """
        logger.info("vector_search", top=top, filters=filters)

        search_kwargs: dict[str, Any] = {
            "search_text": None,
            "vector_queries": [
                VectorizedQuery(
                    vector=query_vector,
                    k_nearest_neighbors=top,
                    fields="content_vector",
                )
            ],
            "top": top,
        }

        if filters:
            search_kwargs["filter"] = filters
        if select:
            search_kwargs["select"] = select

        results = []
        async with self._get_search_client() as client:
            async for result in await client.search(**search_kwargs):
                results.append(dict(result))

        return results

    async def get_document(self, document_id: str) -> dict[str, Any] | None:
        """Retrieve a single document by ID.

        Args:
            document_id: The document's unique identifier.

        Returns:
            The document dict, or None if not found.
        """
        async with self._get_search_client() as client:
            try:
                return cast("dict[str, Any]", await client.get_document(key=document_id))
            except Exception:
                logger.warning("document_not_found", document_id=document_id)
                return None

    async def find_ids(self, odata_filter: str, *, limit: int = 1000) -> list[str]:
        """IDs of the documents matching an OData filter (e.g. all chunks of one file)."""
        async with self._get_search_client() as client:
            results = await client.search(search_text="*", filter=odata_filter, select=["id"], top=limit)
            return [str(r["id"]) async for r in results]

    async def delete_documents(self, document_ids: list[str]) -> None:
        """Delete documents from the index by ID.

        Args:
            document_ids: List of document IDs to delete.
        """
        documents = [{"id": doc_id} for doc_id in document_ids]
        async with self._get_search_client() as client:
            await client.delete_documents(documents=documents)
            logger.info("documents_deleted", count=len(document_ids))
