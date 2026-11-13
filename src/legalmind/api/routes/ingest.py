"""Document ingestion endpoints for uploading and processing legal documents."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from legalmind.api.models.responses import DocumentIngestResponse
from legalmind.ingestion.pipeline import IngestionPipeline

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingestion"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "text/plain",
}

MAX_FILE_SIZE_MB = 50


@router.post("", response_model=DocumentIngestResponse)
async def ingest_document(
    file: UploadFile = File(..., description="The legal document to ingest"),  # noqa: B008
    document_type: str = Form(
        default="contract",
        description="Document type: contract, correspondence, case_file, policy",
    ),
    language: str = Form(default="nl", description="Document language code"),
    title: str | None = Form(default=None, description="Document title"),
    replace_existing: bool = Form(
        default=False, description="Replace earlier uploads with the same title instead of adding a copy"
    ),
) -> DocumentIngestResponse:
    """Upload and process a legal document.

    The document goes through the full ingestion pipeline:
    1. Upload to Azure Blob Storage
    2. Analyze with Content Understanding / Document Intelligence
    3. Chunk into searchable segments
    4. Enrich with NER, key phrases, summaries
    5. Generate embeddings
    6. Index into Azure AI Search
    """
    # Validate content type
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Supported: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    # Read file content
    content = await file.read()

    # Validate file size
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {size_mb:.1f}MB. Maximum: {MAX_FILE_SIZE_MB}MB",
        )

    logger.info(
        "ingest_request",
        filename=file.filename,
        content_type=file.content_type,
        size_mb=round(size_mb, 2),
        document_type=document_type,
    )

    # Run ingestion pipeline
    pipeline = IngestionPipeline()
    result = await pipeline.ingest(
        document_bytes=content,
        filename=file.filename or "document.pdf",
        document_type=document_type,
        language=language,
        title=title,
        content_type=file.content_type or "application/pdf",
        replace_existing=replace_existing,
    )

    return DocumentIngestResponse(
        document_id=result.document_id,
        blob_url=result.blob_url,
        chunk_count=result.chunk_count,
        page_count=result.page_count,
        entities_found=result.metadata.get("entities_found", 0),
        status="completed" if result.is_success else "partial",
    )


@router.post("/batch")
async def ingest_batch(
    files: list[UploadFile] = File(..., description="Multiple legal documents to ingest"),  # noqa: B008
    document_type: str = Form(default="contract"),
    language: str = Form(default="nl"),
) -> dict[str, Any]:
    """Upload and process multiple documents in batch.

    Returns a summary of the batch ingestion results.
    """
    results = []
    errors = []

    for file in files:
        try:
            content = await file.read()
            pipeline = IngestionPipeline()
            result = await pipeline.ingest(
                document_bytes=content,
                filename=file.filename or "document.pdf",
                document_type=document_type,
                language=language,
                content_type=file.content_type or "application/pdf",
            )
            results.append(
                {
                    "filename": file.filename,
                    "document_id": result.document_id,
                    "chunk_count": result.chunk_count,
                    "chunks_indexed": result.chunks_indexed,
                    "status": "completed" if result.is_success else "partial",
                }
            )
        except Exception as e:
            logger.error("batch_ingest_error", filename=file.filename, error=str(e))
            errors.append(
                {
                    "filename": file.filename,
                    "error": str(e),
                    "status": "failed",
                }
            )

    return {
        "total": len(files),
        "succeeded": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
