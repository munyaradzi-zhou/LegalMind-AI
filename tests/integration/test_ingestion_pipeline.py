"""Integration tests for the document ingestion pipeline.

Requires Azure credentials and live Azure services.
Run with: pytest tests/integration/ -m integration
"""

from __future__ import annotations

import pytest

from legalmind.ingestion.pipeline import IngestionPipeline


@pytest.mark.integration
class TestIngestionPipeline:
    """End-to-end tests for document ingestion with live Azure services."""

    @pytest.mark.asyncio
    async def test_ingest_pdf_document(self, sample_document_bytes: bytes):
        """Test ingesting a PDF document through the full pipeline."""
        pipeline = IngestionPipeline()

        result = await pipeline.ingest(
            document_bytes=sample_document_bytes,
            filename="test_contract.pdf",
            document_type="contract",
            language="nl",
            content_type="application/pdf",
        )

        assert result["document_id"]
        assert result["chunk_count"] > 0
        assert result["page_count"] >= 1
        assert result["blob_url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_ingest_stores_in_blob(self, sample_document_bytes: bytes):
        """Test that the document is stored in Blob Storage."""
        from legalmind.services.blob_client import BlobStorageService

        pipeline = IngestionPipeline()
        await pipeline.ingest(
            document_bytes=sample_document_bytes,
            filename="test_storage.pdf",
            document_type="contract",
            language="nl",
        )

        blob_service = BlobStorageService()
        blobs = await blob_service.list_documents(prefix="contract")
        blob_names = [b["name"] for b in blobs]
        assert any("test_storage" in name for name in blob_names)

    @pytest.mark.asyncio
    async def test_ingest_creates_search_chunks(self, sample_document_bytes: bytes):
        """Test that chunks are indexed in Azure AI Search."""
        from legalmind.services.search_client import SearchService

        pipeline = IngestionPipeline()
        result = await pipeline.ingest(
            document_bytes=sample_document_bytes,
            filename="test_search.pdf",
            document_type="contract",
            language="nl",
        )

        search_service = SearchService()
        results = await search_service.hybrid_search(
            query="test",
            filters=f"document_id eq '{result['document_id']}'",
            top=5,
        )
        assert len(results) > 0
