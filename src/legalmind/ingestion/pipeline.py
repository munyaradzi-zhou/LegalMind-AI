"""Main document ingestion pipeline orchestrator.

Coordinates the full ingestion flow for Dutch legal documents:
1. Upload raw document to Azure Blob Storage.
2. Analyze with Azure Content Understanding or Document Intelligence.
3. Chunk the extracted text into retrieval-optimized segments.
4. Enrich chunks with NER, key phrases, and summaries.
5. Generate vector embeddings for each chunk.
6. Index enriched, embedded chunks into Azure AI Search.

Returns an :class:`IngestionResult` summarizing the operation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from legalmind.ingestion.chunking import Chunk, parent_child_chunk, semantic_chunk
from legalmind.ingestion.content_understanding import ContentUnderstandingClient, LegalDocumentType
from legalmind.ingestion.document_context import add_context_header, describe_document
from legalmind.ingestion.document_intelligence import DocumentIntelligenceAnalyzer
from legalmind.ingestion.embedding import EmbeddedChunk, EmbeddingGenerator
from legalmind.ingestion.enrichment import ChunkEnricher, EnrichedChunk
from legalmind.ingestion.indexing import IndexingResult, SearchIndexer
from legalmind.services import AzureOpenAIService, BlobStorageService, CognitiveServicesClient, SearchService

logger = structlog.get_logger(__name__)


class AnalysisBackend(StrEnum):
    """Which Azure service to use for document analysis."""

    CONTENT_UNDERSTANDING = "content_understanding"
    DOCUMENT_INTELLIGENCE = "document_intelligence"


class ChunkingStrategy(StrEnum):
    """Which chunking strategy to apply."""

    SEMANTIC = "semantic"
    PARENT_CHILD = "parent_child"


@dataclass
class IngestionResult:
    """Result returned by the ingestion pipeline.

    Attributes:
        document_id: Unique identifier assigned to the ingested document.
        blob_url: URL of the uploaded document in Azure Blob Storage.
        chunk_count: Total number of chunks created.
        chunks_indexed: Number of chunks successfully indexed.
        chunks_failed: Number of chunks that failed to index.
        page_count: Number of pages in the document.
        analysis_backend: Which analysis service was used.
        chunking_strategy: Which chunking strategy was applied.
        duration_seconds: Total pipeline duration in seconds.
        metadata: Additional metadata about the ingestion.
    """

    document_id: str
    blob_url: str = ""
    chunk_count: int = 0
    chunks_indexed: int = 0
    chunks_failed: int = 0
    page_count: int = 0
    analysis_backend: str = ""
    chunking_strategy: str = ""
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Whether the ingestion completed without failures."""
        return self.chunks_failed == 0 and self.chunk_count > 0


class IngestionPipeline:
    """Orchestrates the end-to-end document ingestion flow.

    Accepts raw document bytes and metadata, runs them through analysis,
    chunking, enrichment, embedding, and indexing stages, and returns a
    result summary.

    Usage::

        pipeline = IngestionPipeline()
        result = await pipeline.ingest(
            document_bytes=pdf_bytes,
            filename="contract_2024.pdf",
            content_type="application/pdf",
            document_type=LegalDocumentType.CONTRACT,
        )
        print(f"Ingested {result.chunk_count} chunks for document {result.document_id}")
    """

    def __init__(
        self,
        *,
        blob_service: BlobStorageService | None = None,
        content_understanding: ContentUnderstandingClient | None = None,
        doc_intelligence: DocumentIntelligenceAnalyzer | None = None,
        cognitive_client: CognitiveServicesClient | None = None,
        openai_service: AzureOpenAIService | None = None,
        search_service: SearchService | None = None,
        analysis_backend: AnalysisBackend = AnalysisBackend.DOCUMENT_INTELLIGENCE,
        chunking_strategy: ChunkingStrategy = ChunkingStrategy.SEMANTIC,
        embedding_batch_size: int = 16,
        enable_enrichment: bool = True,
    ) -> None:
        """Initialize the ingestion pipeline.

        All service clients are optional — if not provided, new instances are
        created using the application configuration.

        Args:
            blob_service: Azure Blob Storage client.
            content_understanding: Content Understanding client.
            doc_intelligence: Document Intelligence analyzer.
            cognitive_client: Cognitive Services client (for NER/enrichment).
            openai_service: Azure OpenAI client (for embeddings).
            search_service: Azure AI Search client.
            analysis_backend: Which analysis backend to use.
            chunking_strategy: Which chunking strategy to apply.
            embedding_batch_size: Batch size for embedding generation.
            enable_enrichment: Whether to run NER/key phrase enrichment.
        """
        self._blob = blob_service or BlobStorageService()
        self._content_understanding = content_understanding or ContentUnderstandingClient()
        self._doc_intelligence = doc_intelligence or DocumentIntelligenceAnalyzer()
        self._enricher = ChunkEnricher(
            cognitive_client or CognitiveServicesClient(),
            include_summary=enable_enrichment,
        )
        self._openai = openai_service or AzureOpenAIService()
        self._embedder = EmbeddingGenerator(self._openai, batch_size=embedding_batch_size)
        self._search = search_service or SearchService()
        self._indexer = SearchIndexer(self._search)

        self._analysis_backend = analysis_backend
        self._chunking_strategy = chunking_strategy
        self._enable_enrichment = enable_enrichment

    async def ingest(
        self,
        document_bytes: bytes,
        *,
        filename: str,
        content_type: str = "application/pdf",
        document_type: LegalDocumentType | str = LegalDocumentType.GENERAL,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        language: str = "nl",
        title: str | None = None,
        replace_existing: bool = False,
    ) -> IngestionResult:
        """Run the full ingestion pipeline on a document.

        Args:
            document_bytes: Raw document bytes (PDF, DOCX, TIFF, images).
            filename: Original filename of the document.
            content_type: MIME type of the document.
            document_type: Type of legal document. Stored as-is for filtering; types
                without a dedicated analyzer (e.g. ``case_file``) are analyzed as
                ``general``.
            document_id: Optional pre-assigned document ID. Generated if not provided.
            metadata: Additional metadata to store with the document and chunks.
            language: Language code for NER and enrichment (default: Dutch).
            title: Human-readable document title. Defaults to the filename.
            replace_existing: Remove the chunks of earlier uploads with the same title
                once this version is indexed (re-ingesting a document doesn't duplicate it).

        Returns:
            An :class:`IngestionResult` summarizing the operation.

        Raises:
            RuntimeError: If a critical pipeline stage fails.
        """
        start_time = time.monotonic()
        doc_id = document_id or str(uuid.uuid4())
        document_type_value = str(document_type)
        try:
            analysis_type = LegalDocumentType(document_type_value)
        except ValueError:
            analysis_type = LegalDocumentType.GENERAL

        doc_metadata = metadata or {}
        doc_metadata["filename"] = filename
        doc_metadata["title"] = title or doc_metadata.get("title") or filename
        doc_metadata["content_type"] = content_type
        doc_metadata["document_type"] = document_type_value
        doc_metadata["language"] = language

        logger.info(
            "ingestion_start",
            document_id=doc_id,
            filename=filename,
            content_type=content_type,
            document_type=document_type_value,
            size_bytes=len(document_bytes),
            analysis_backend=self._analysis_backend.value,
            chunking_strategy=self._chunking_strategy.value,
        )

        result = IngestionResult(
            document_id=doc_id,
            analysis_backend=self._analysis_backend.value,
            chunking_strategy=self._chunking_strategy.value,
            metadata=doc_metadata,
        )

        try:
            # ── Stage 1: Upload to Blob Storage ──────────────────────────
            blob_url = await self._upload_document(doc_id, document_bytes, filename, content_type, doc_metadata)
            result.blob_url = blob_url
            doc_metadata["blob_url"] = blob_url

            # ── Stage 2: Analyze document ────────────────────────────────
            analysis = await self._analyze_document(document_bytes, analysis_type, content_type)
            result.page_count = analysis.get("page_count", 0)

            # ── Stage 3: Chunk the document ──────────────────────────────
            chunks = self._chunk_document(analysis, doc_id, doc_metadata)
            result.chunk_count = len(chunks)

            if not chunks:
                logger.warning("ingestion_no_chunks", document_id=doc_id)
                result.duration_seconds = time.monotonic() - start_time
                return result

            # ── Stage 3b: Contextual header on every chunk ───────────────
            description = await describe_document(self._openai, analysis.get("content", ""), doc_metadata["title"])
            add_context_header(chunks, description)
            result.metadata["document_context"] = description

            # ── Stage 4: Enrich chunks ───────────────────────────────────
            enriched_chunks = await self._enrich_chunks(chunks, language)
            result.metadata["entities_found"] = sum(len(e.entities) for e in enriched_chunks)

            # ── Stage 5: Generate embeddings ─────────────────────────────
            embedded_chunks = await self._generate_embeddings(chunks)

            # ── Stage 6: Index into Azure AI Search ──────────────────────
            indexing_result = await self._index_chunks(enriched_chunks, embedded_chunks, doc_metadata)
            result.chunks_indexed = indexing_result.uploaded
            result.chunks_failed = indexing_result.failed

            # ── Stage 7: Remove earlier versions (only after this one is indexed) ──
            if replace_existing and result.is_success:
                result.metadata["chunks_replaced"] = await self._remove_earlier_versions(doc_metadata["title"], doc_id)

        except Exception:
            logger.exception("ingestion_failed", document_id=doc_id, filename=filename)
            raise

        result.duration_seconds = time.monotonic() - start_time

        logger.info(
            "ingestion_complete",
            document_id=doc_id,
            chunk_count=result.chunk_count,
            chunks_indexed=result.chunks_indexed,
            chunks_failed=result.chunks_failed,
            page_count=result.page_count,
            duration_seconds=round(result.duration_seconds, 2),
        )

        return result

    # ─── Pipeline Stages ─────────────────────────────────────────────────

    async def _upload_document(
        self,
        document_id: str,
        document_bytes: bytes,
        filename: str,
        content_type: str,
        metadata: dict[str, Any],
    ) -> str:
        """Stage 1: Upload the raw document to Azure Blob Storage.

        Stores the document under a path like ``documents/{document_id}/{filename}``.

        Args:
            document_id: Unique document identifier.
            document_bytes: Raw file bytes.
            filename: Original filename.
            content_type: MIME type.
            metadata: Blob metadata tags.

        Returns:
            The blob URL.
        """
        blob_name = f"documents/{document_id}/{filename}"
        blob_metadata = {
            "document_id": document_id,
            "document_type": metadata.get("document_type", "general"),
            "language": metadata.get("language", "nl"),
        }

        logger.info("upload_stage_start", document_id=document_id, blob_name=blob_name)

        blob_url = await self._blob.upload_document(
            blob_name,
            document_bytes,
            content_type=content_type,
            metadata=blob_metadata,
        )

        logger.info("upload_stage_complete", document_id=document_id, blob_url=blob_url)
        return blob_url

    async def _analyze_document(
        self,
        document_bytes: bytes,
        document_type: LegalDocumentType,
        content_type: str,
    ) -> dict[str, Any]:
        """Stage 2: Analyze the document using the configured backend.

        Args:
            document_bytes: Raw document bytes.
            document_type: Legal document type for analyzer selection.
            content_type: MIME type.

        Returns:
            Analysis result dictionary with ``content``, ``pages``,
            ``tables``, ``paragraphs``, and ``page_count``.
        """
        logger.info("analysis_stage_start", backend=self._analysis_backend.value)

        if content_type == "text/plain":
            # Neither analysis service reads plain text; it needs no layout analysis anyway.
            text = document_bytes.decode("utf-8", errors="replace")
            logger.info("analysis_stage_complete", page_count=1, content_length=len(text), backend="plain_text")
            return {"content": text, "pages": [], "tables": [], "paragraphs": [], "page_count": 1}

        if self._analysis_backend == AnalysisBackend.CONTENT_UNDERSTANDING:
            result = await self._content_understanding.analyze_document(
                document_bytes,
                document_type=document_type,
                content_type=content_type,
            )
        else:
            di_result = await self._doc_intelligence.analyze(document_bytes)
            result = {
                "content": di_result.content,
                "pages": [
                    {
                        "page_number": p.page_number,
                        "text": p.text,
                        "lines": p.lines,
                    }
                    for p in di_result.pages
                ],
                "tables": [
                    {
                        "row_count": t.row_count,
                        "column_count": t.column_count,
                        "cells": t.cells,
                        "page_numbers": t.page_numbers,
                    }
                    for t in di_result.tables
                ],
                "paragraphs": di_result.paragraphs,
                "page_count": di_result.page_count,
                "key_value_pairs": [
                    {"key": kv.key, "value": kv.value, "confidence": kv.confidence} for kv in di_result.key_value_pairs
                ],
            }

        logger.info(
            "analysis_stage_complete",
            page_count=result.get("page_count", 0),
            content_length=len(result.get("content", "")),
        )

        return result

    def _chunk_document(
        self,
        analysis: dict[str, Any],
        document_id: str,
        metadata: dict[str, Any],
    ) -> list[Chunk]:
        """Stage 3: Chunk the analyzed document content.

        Args:
            analysis: Analysis result from stage 2.
            document_id: Document identifier.
            metadata: Metadata to attach to each chunk.

        Returns:
            List of chunks.
        """
        full_text = analysis.get("content", "")
        paragraphs = analysis.get("paragraphs", [])

        if not full_text.strip():
            logger.warning("chunk_stage_no_content", document_id=document_id)
            return []

        logger.info(
            "chunk_stage_start",
            strategy=self._chunking_strategy.value,
            content_length=len(full_text),
        )

        # Append table content as markdown to provide structured data context
        table_text = self._tables_to_text(analysis.get("tables", []))
        if table_text:
            full_text = full_text + "\n\n" + table_text

        chunk_metadata = {k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}

        if self._chunking_strategy == ChunkingStrategy.PARENT_CHILD:
            chunks = parent_child_chunk(
                full_text,
                document_id,
                paragraphs=paragraphs,
                metadata=chunk_metadata,
            )
        else:
            chunks = semantic_chunk(
                full_text,
                document_id,
                paragraphs=paragraphs,
                metadata=chunk_metadata,
            )

        logger.info(
            "chunk_stage_complete",
            chunk_count=len(chunks),
            strategy=self._chunking_strategy.value,
        )

        return chunks

    async def _enrich_chunks(
        self,
        chunks: list[Chunk],
        language: str,
    ) -> list[EnrichedChunk]:
        """Stage 4: Enrich chunks with NER, key phrases, and summaries.

        If enrichment is disabled, returns minimal :class:`EnrichedChunk`
        wrappers without NER data.

        Args:
            chunks: Chunks to enrich.
            language: Language code for NER.

        Returns:
            List of enriched chunks.
        """
        if not self._enable_enrichment:
            logger.info("enrichment_stage_skipped")
            return [EnrichedChunk(chunk=c) for c in chunks]

        logger.info("enrichment_stage_start", chunk_count=len(chunks))
        enriched = await self._enricher.enrich_chunks(chunks, language=language)
        logger.info("enrichment_stage_complete", chunk_count=len(enriched))
        return enriched

    async def _generate_embeddings(
        self,
        chunks: list[Chunk],
    ) -> list[EmbeddedChunk]:
        """Stage 5: Generate vector embeddings for all chunks.

        Args:
            chunks: Chunks to embed.

        Returns:
            List of embedded chunks.
        """
        logger.info("embedding_stage_start", chunk_count=len(chunks))
        embedded = await self._embedder.embed_chunks(chunks)
        logger.info("embedding_stage_complete", chunk_count=len(embedded))
        return embedded

    async def _index_chunks(
        self,
        enriched_chunks: list[EnrichedChunk],
        embedded_chunks: list[EmbeddedChunk],
        document_metadata: dict[str, Any],
    ) -> IndexingResult:
        """Stage 6: Index enriched, embedded chunks into Azure AI Search.

        Args:
            enriched_chunks: Enriched chunks.
            embedded_chunks: Embedded chunks.
            document_metadata: Document-level metadata.

        Returns:
            An :class:`IndexingResult`.
        """
        logger.info("indexing_stage_start", chunk_count=len(enriched_chunks))
        result = await self._indexer.index_document(
            enriched_chunks,
            embedded_chunks,
            document_metadata=document_metadata,
        )
        logger.info(
            "indexing_stage_complete",
            uploaded=result.uploaded,
            failed=result.failed,
        )
        return result

    async def _remove_earlier_versions(self, title: str, document_id: str) -> int:
        """Delete the chunks of other documents with the same title. Returns how many."""
        escaped = title.replace("'", "''")
        ids = await self._search.find_ids(f"title eq '{escaped}' and document_id ne '{document_id}'")
        if ids:
            await self._search.delete_documents(ids)
        logger.info("earlier_versions_removed", title=title, chunks=len(ids))
        return len(ids)

    @staticmethod
    def _tables_to_text(tables: list[dict[str, Any]]) -> str:
        """Convert extracted tables to Markdown text for inclusion in chunks.

        Args:
            tables: List of table dicts from analysis.

        Returns:
            Markdown-formatted tables as a single string.
        """
        if not tables:
            return ""

        parts: list[str] = []
        for i, table in enumerate(tables):
            row_count = table.get("row_count", 0)
            col_count = table.get("column_count", 0)
            cells = table.get("cells", [])

            if row_count == 0 or col_count == 0:
                continue

            # Build grid
            grid: list[list[str]] = [[""] * col_count for _ in range(row_count)]
            for cell in cells:
                r = cell.get("row_index", 0)
                c = cell.get("column_index", 0)
                if 0 <= r < row_count and 0 <= c < col_count:
                    grid[r][c] = cell.get("content", "").replace("\n", " ")

            lines: list[str] = [f"**Table {i + 1}**"]
            lines.append("| " + " | ".join(grid[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
            for row in grid[1:]:
                lines.append("| " + " | ".join(row) + " |")

            parts.append("\n".join(lines))

        return "\n\n".join(parts)
