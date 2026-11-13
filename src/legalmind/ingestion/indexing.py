"""Azure AI Search indexer for document chunks.

Converts enriched, embedded chunks into Azure AI Search documents and uploads
them in batches. Provides index statistics and document management utilities.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.services import SearchService

if TYPE_CHECKING:
    from legalmind.ingestion.chunking import Chunk
    from legalmind.ingestion.embedding import EmbeddedChunk
    from legalmind.ingestion.enrichment import EnrichedChunk

logger = structlog.get_logger(__name__)

# Maximum number of documents per Azure AI Search upload batch
_UPLOAD_BATCH_SIZE = 100


class SearchIndexer:
    """Indexes enriched document chunks into Azure AI Search.

    Converts :class:`EnrichedChunk` and :class:`EmbeddedChunk` objects into
    search documents with the required schema, then uploads them in batches.

    The search document schema matches the index definition expected by
    LegalMind's Azure AI Search index:

    - ``id``: Unique chunk identifier.
    - ``document_id``: Source document identifier.
    - ``content``: Chunk text content.
    - ``content_vector``: Embedding vector (3072 dimensions).
    - ``section_title``: Title of the document section.
    - ``page_number``: Primary page number.
    - ``page_numbers``: All page numbers spanned by the chunk.
    - ``chunk_index``: Position of this chunk within the document.
    - ``chunk_type``: Type of chunk (``chunk``, ``parent``, ``child``).
    - ``parent_id``: Parent chunk ID (for child chunks).
    - ``entities_persons``: Person names extracted from the chunk.
    - ``entities_organizations``: Organization names extracted.
    - ``entities_dates``: Date references extracted.
    - ``entities_monetary``: Monetary amounts extracted.
    - ``key_phrases``: Key phrases extracted from the chunk.
    - ``summary``: Extractive summary of the chunk.
    - ``metadata``: Additional metadata as a JSON string.
    - ``indexed_at``: Timestamp of indexing.

    Usage::

        indexer = SearchIndexer()
        stats = await indexer.index_document(enriched_chunks, embedded_chunks)
    """

    def __init__(
        self,
        search_service: SearchService | None = None,
        *,
        batch_size: int = _UPLOAD_BATCH_SIZE,
    ) -> None:
        """Initialize the search indexer.

        Args:
            search_service: An existing ``SearchService`` instance.
                If ``None``, a new one is created.
            batch_size: Number of documents to upload per batch.
        """
        self._search = search_service or SearchService()
        self._batch_size = min(batch_size, 1000)

    async def index_document(
        self,
        enriched_chunks: list[EnrichedChunk],
        embedded_chunks: list[EmbeddedChunk],
        *,
        document_metadata: dict[str, Any] | None = None,
    ) -> IndexingResult:
        """Index a full document's enriched and embedded chunks.

        Pairs enrichment data with embedding vectors and uploads the resulting
        search documents in batches.

        Args:
            enriched_chunks: Enriched chunks with NER and key phrases.
            embedded_chunks: The same chunks with embedding vectors.
            document_metadata: Optional document-level metadata to include
                in every search document.

        Returns:
            An :class:`IndexingResult` with upload statistics.

        Raises:
            ValueError: If enriched and embedded chunk counts do not match.
        """
        if len(enriched_chunks) != len(embedded_chunks):
            msg = (
                f"Enriched chunk count ({len(enriched_chunks)}) does not match "
                f"embedded chunk count ({len(embedded_chunks)})"
            )
            raise ValueError(msg)

        doc_meta = document_metadata or {}

        logger.info(
            "index_document_start",
            chunk_count=len(enriched_chunks),
            batch_size=self._batch_size,
        )

        # Build search documents
        search_docs = self._build_search_documents(
            enriched_chunks,
            embedded_chunks,
            doc_meta,
        )

        # Upload in batches
        total_uploaded = 0
        total_failed = 0

        for batch_start in range(0, len(search_docs), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(search_docs))
            batch = search_docs[batch_start:batch_end]

            try:
                succeeded = await self._upload_batch(batch)
                total_uploaded += succeeded
                total_failed += len(batch) - succeeded
            except Exception:
                logger.exception(
                    "index_batch_failed",
                    batch_start=batch_start,
                    batch_size=len(batch),
                )
                total_failed += len(batch)

            logger.debug(
                "index_batch_complete",
                batch_start=batch_start,
                batch_size=len(batch),
                progress=f"{batch_end}/{len(search_docs)}",
            )

        result = IndexingResult(
            total_chunks=len(search_docs),
            uploaded=total_uploaded,
            failed=total_failed,
            document_id=enriched_chunks[0].chunk.document_id if enriched_chunks else "",
        )

        logger.info(
            "index_document_complete",
            document_id=result.document_id,
            total=result.total_chunks,
            uploaded=result.uploaded,
            failed=result.failed,
        )

        return result

    async def index_chunks_simple(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> IndexingResult:
        """Index chunks with embeddings only (no enrichment).

        A simplified path for cases where NER enrichment is not needed.

        Args:
            chunks: List of chunks.
            embeddings: List of embedding vectors, one per chunk.

        Returns:
            An :class:`IndexingResult`.
        """
        if len(chunks) != len(embeddings):
            msg = f"Chunk count ({len(chunks)}) does not match embedding count ({len(embeddings)})"
            raise ValueError(msg)

        now = datetime.now(UTC).isoformat()

        search_docs: list[dict[str, Any]] = []
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            search_docs.append(
                {
                    "id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "content_vector": embedding,
                    "title": str(chunk.metadata.get("title") or chunk.metadata.get("filename", "")),
                    "document_type": str(chunk.metadata.get("document_type", "general")),
                    "language": str(chunk.metadata.get("language", "nl")),
                    "page_number": chunk.page_number,
                    "section_title": chunk.section_title,
                    "parent_id": chunk.parent_id or "",
                    "source_url": str(chunk.metadata.get("blob_url", "")),
                    "created_at": now,
                }
            )

        total_uploaded = 0
        total_failed = 0

        for batch_start in range(0, len(search_docs), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(search_docs))
            batch = search_docs[batch_start:batch_end]
            try:
                succeeded = await self._upload_batch(batch)
                total_uploaded += succeeded
                total_failed += len(batch) - succeeded
            except Exception:
                logger.exception("index_batch_failed", batch_start=batch_start)
                total_failed += len(batch)

        return IndexingResult(
            total_chunks=len(search_docs),
            uploaded=total_uploaded,
            failed=total_failed,
            document_id=chunks[0].document_id if chunks else "",
        )

    async def delete_document_chunks(self, document_id: str, chunk_ids: list[str]) -> int:
        """Delete all chunks for a document from the search index.

        Args:
            document_id: The document ID (for logging).
            chunk_ids: List of chunk IDs to delete.

        Returns:
            Number of chunks deleted.
        """
        if not chunk_ids:
            return 0

        logger.info("delete_document_chunks", document_id=document_id, count=len(chunk_ids))
        await self._search.delete_documents(chunk_ids)
        return len(chunk_ids)

    async def get_index_stats(self) -> dict[str, Any]:
        """Retrieve statistics about the search index.

        Returns:
            Dictionary with index statistics including document count
            and storage size.
        """
        try:
            index_client = self._search._get_index_client()
            stats = index_client.get_index_statistics(self._search._index_name)
            result = {
                "document_count": stats.document_count,
                "storage_size_bytes": stats.storage_size,
                "index_name": self._search._index_name,
            }
            logger.info("index_stats", **result)
            return result
        except Exception:
            logger.exception("get_index_stats_failed")
            return {
                "document_count": -1,
                "storage_size_bytes": -1,
                "index_name": self._search._index_name,
                "error": "Failed to retrieve statistics",
            }

    # ─── Internal Methods ────────────────────────────────────────────────

    @staticmethod
    def _build_search_documents(
        enriched_chunks: list[EnrichedChunk],
        embedded_chunks: list[EmbeddedChunk],
        document_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build search document dicts from enriched and embedded chunks.

        Args:
            enriched_chunks: Enriched chunks with NER data.
            embedded_chunks: Chunks with embedding vectors.
            document_metadata: Document-level metadata.

        Returns:
            List of search document dictionaries.
        """
        now = datetime.now(UTC).isoformat()
        search_docs: list[dict[str, Any]] = []

        for enriched, embedded in zip(enriched_chunks, embedded_chunks, strict=False):
            chunk = enriched.chunk

            # Merge chunk metadata with document metadata
            merged_meta = {**document_metadata, **chunk.metadata}

            search_doc: dict[str, Any] = {
                # Core fields
                "id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "content_vector": embedded.embedding,
                # Document metadata
                "title": str(merged_meta.get("title") or merged_meta.get("filename", "")),
                "document_type": str(merged_meta.get("document_type", "general")),
                "language": str(merged_meta.get("language", "nl")),
                "page_number": chunk.page_number,
                "section_title": chunk.section_title,
                # NER enrichment, key phrases and summary
                "entities": _unique_entity_texts(enriched.entities),
                "key_phrases": enriched.key_phrases,
                "summary": enriched.summary,
                # Hierarchy and provenance
                "parent_id": chunk.parent_id or "",
                "source_url": str(merged_meta.get("blob_url", "")),
                "created_at": now,
            }

            search_docs.append(search_doc)

        return search_docs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _upload_batch(self, documents: list[dict[str, Any]]) -> int:
        """Upload a batch of documents to the search index with retry.

        Args:
            documents: Batch of search documents to upload.

        Returns:
            Number of documents the index accepted.
        """
        return await self._search.upload_documents(documents)


def _unique_entity_texts(entities: list[dict[str, Any]]) -> list[str]:
    """Flatten NER entity dicts into a sorted list of unique entity texts for the index."""
    return sorted({e["text"] for e in entities if e.get("text")})


class IndexingResult:
    """Result of an indexing operation.

    Attributes:
        total_chunks: Total number of chunks processed.
        uploaded: Number of chunks successfully uploaded.
        failed: Number of chunks that failed to upload.
        document_id: The source document ID.
    """

    __slots__ = ("document_id", "failed", "total_chunks", "uploaded")

    def __init__(
        self,
        *,
        total_chunks: int,
        uploaded: int,
        failed: int,
        document_id: str,
    ) -> None:
        self.total_chunks = total_chunks
        self.uploaded = uploaded
        self.failed = failed
        self.document_id = document_id

    @property
    def success_rate(self) -> float:
        """Fraction of chunks successfully uploaded (0.0 to 1.0)."""
        if self.total_chunks == 0:
            return 1.0
        return self.uploaded / self.total_chunks

    @property
    def is_complete(self) -> bool:
        """Whether all chunks were uploaded successfully."""
        return self.failed == 0

    def __repr__(self) -> str:
        return (
            f"IndexingResult(document_id={self.document_id!r}, "
            f"total={self.total_chunks}, uploaded={self.uploaded}, "
            f"failed={self.failed})"
        )
