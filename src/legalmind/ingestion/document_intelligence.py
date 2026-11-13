"""Azure Document Intelligence wrapper for legal document layout extraction.

Uses the ``azure-ai-formrecognizer`` SDK with the ``prebuilt-layout`` model to
extract text, tables, and key-value pairs from scanned or digital legal
documents (PDF, DOCX, TIFF, images).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from azure.ai.formrecognizer.aio import DocumentAnalysisClient
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PageContent:
    """Extracted content for a single page."""

    page_number: int
    width: float
    height: float
    unit: str
    text: str
    lines: list[str] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    selection_marks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TableContent:
    """Extracted table with cells."""

    row_count: int
    column_count: int
    cells: list[dict[str, Any]] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the table as a Markdown string for embedding into chunk text.

        Returns:
            A Markdown-formatted table string.
        """
        if self.row_count == 0 or self.column_count == 0:
            return ""

        # Build a 2D grid
        grid: list[list[str]] = [[""] * self.column_count for _ in range(self.row_count)]
        for cell in self.cells:
            r = cell.get("row_index", 0)
            c = cell.get("column_index", 0)
            if 0 <= r < self.row_count and 0 <= c < self.column_count:
                grid[r][c] = cell.get("content", "").replace("\n", " ")

        lines: list[str] = []
        # Header row
        lines.append("| " + " | ".join(grid[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in range(self.column_count)) + " |")
        # Data rows
        for row in grid[1:]:
            lines.append("| " + " | ".join(row) + " |")

        return "\n".join(lines)


@dataclass(frozen=True)
class KeyValuePair:
    """A key-value pair extracted from the document."""

    key: str
    value: str
    confidence: float
    page_number: int | None = None


@dataclass(frozen=True)
class DocumentIntelligenceResult:
    """Complete extraction result from Document Intelligence.

    Attributes:
        content: The full extracted text of the document.
        pages: Per-page extracted content.
        tables: Extracted tables.
        key_value_pairs: Extracted key-value pairs.
        paragraphs: Extracted paragraphs with roles (title, sectionHeading, etc.).
        page_count: Total number of pages.
    """

    content: str
    pages: list[PageContent]
    tables: list[TableContent]
    key_value_pairs: list[KeyValuePair]
    paragraphs: list[dict[str, Any]]
    page_count: int


class DocumentIntelligenceAnalyzer:
    """Wrapper around Azure Document Intelligence for layout extraction.

    Uses the ``prebuilt-layout`` model by default, which extracts:
    - Text and reading order
    - Tables with cell content and structure
    - Selection marks (checkboxes)
    - Key-value pairs
    - Section headings and paragraphs

    Usage::

        analyzer = DocumentIntelligenceAnalyzer()
        result = await analyzer.analyze(pdf_bytes)
        for page in result.pages:
            print(f"Page {page.page_number}: {page.text[:200]}")
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._endpoint = settings.cognitive.document_intelligence_endpoint

    def _get_client(self) -> DocumentAnalysisClient:
        """Create a new async Document Analysis client."""
        return DocumentAnalysisClient(
            endpoint=self._endpoint,
            credential=self._credential,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=60))
    async def analyze(
        self,
        document_bytes: bytes,
        *,
        model_id: str = "prebuilt-layout",
        locale: str = "nl-NL",
    ) -> DocumentIntelligenceResult:
        """Analyze a document using Azure Document Intelligence.

        Args:
            document_bytes: Raw document bytes (PDF, DOCX, TIFF, JPEG, PNG, BMP).
            model_id: The model to use. Defaults to ``prebuilt-layout``.
            locale: Locale hint for OCR. Defaults to Dutch (``nl-NL``).

        Returns:
            A :class:`DocumentIntelligenceResult` with all extracted content.

        Raises:
            azure.core.exceptions.HttpResponseError: On API errors.
        """
        logger.info(
            "document_intelligence_analyze_start",
            model_id=model_id,
            locale=locale,
            size_bytes=len(document_bytes),
        )

        async with self._get_client() as client:
            poller = await client.begin_analyze_document(
                model_id,
                document_bytes,
                locale=locale,
            )
            result = await poller.result()

        pages = self._extract_pages(result)
        tables = self._extract_tables(result)
        key_value_pairs = self._extract_key_value_pairs(result)
        paragraphs = self._extract_paragraphs(result)

        doc_result = DocumentIntelligenceResult(
            content=result.content or "",
            pages=pages,
            tables=tables,
            key_value_pairs=key_value_pairs,
            paragraphs=paragraphs,
            page_count=len(pages),
        )

        logger.info(
            "document_intelligence_analyze_complete",
            page_count=doc_result.page_count,
            table_count=len(tables),
            kv_pair_count=len(key_value_pairs),
            paragraph_count=len(paragraphs),
        )

        return doc_result

    @staticmethod
    def _extract_pages(result: Any) -> list[PageContent]:
        """Extract per-page content from the analysis result."""
        pages: list[PageContent] = []

        for page in result.pages:
            lines = [line.content for line in (page.lines or [])]
            page_text = "\n".join(lines)

            words = [
                {
                    "content": word.content,
                    "confidence": word.confidence,
                    "polygon": word.polygon,
                }
                for word in (page.words or [])
            ]

            selection_marks = [
                {
                    "state": mark.state,
                    "confidence": mark.confidence,
                    "polygon": mark.polygon,
                }
                for mark in (page.selection_marks or [])
            ]

            pages.append(
                PageContent(
                    page_number=page.page_number,
                    width=page.width or 0,
                    height=page.height or 0,
                    unit=page.unit or "inch",
                    text=page_text,
                    lines=lines,
                    words=words,
                    selection_marks=selection_marks,
                )
            )

        return pages

    @staticmethod
    def _extract_tables(result: Any) -> list[TableContent]:
        """Extract tables from the analysis result."""
        tables: list[TableContent] = []

        for table in result.tables or []:
            cells = [
                {
                    "row_index": cell.row_index,
                    "column_index": cell.column_index,
                    "content": cell.content,
                    "kind": cell.kind,
                    "row_span": cell.row_span,
                    "column_span": cell.column_span,
                }
                for cell in table.cells
            ]

            # Collect unique page numbers from cell bounding regions
            page_numbers: list[int] = []
            for cell in table.cells:
                for region in cell.bounding_regions or []:
                    if region.page_number not in page_numbers:
                        page_numbers.append(region.page_number)
            page_numbers.sort()

            tables.append(
                TableContent(
                    row_count=table.row_count,
                    column_count=table.column_count,
                    cells=cells,
                    page_numbers=page_numbers,
                )
            )

        return tables

    @staticmethod
    def _extract_key_value_pairs(result: Any) -> list[KeyValuePair]:
        """Extract key-value pairs from the analysis result."""
        pairs: list[KeyValuePair] = []

        for kv in result.key_value_pairs or []:
            key_text = kv.key.content if kv.key else ""
            value_text = kv.value.content if kv.value else ""
            confidence = kv.confidence or 0.0

            # Determine page number from bounding regions
            page_number: int | None = None
            if kv.key and kv.key.bounding_regions:
                page_number = kv.key.bounding_regions[0].page_number

            pairs.append(
                KeyValuePair(
                    key=key_text,
                    value=value_text,
                    confidence=confidence,
                    page_number=page_number,
                )
            )

        return pairs

    @staticmethod
    def _extract_paragraphs(result: Any) -> list[dict[str, Any]]:
        """Extract paragraphs with their semantic roles.

        Paragraph roles include: title, sectionHeading, footnote, pageHeader,
        pageFooter, pageNumber, and None (regular paragraph).
        """
        paragraphs: list[dict[str, Any]] = []

        for para in result.paragraphs or []:
            # Determine the page number(s) for this paragraph
            page_numbers: list[int] = []
            for region in para.bounding_regions or []:
                if region.page_number not in page_numbers:
                    page_numbers.append(region.page_number)

            paragraphs.append(
                {
                    "content": para.content,
                    "role": para.role,
                    "page_numbers": sorted(page_numbers),
                }
            )

        return paragraphs

    async def analyze_batch(
        self,
        documents: list[tuple[str, bytes]],
        *,
        model_id: str = "prebuilt-layout",
        locale: str = "nl-NL",
        max_concurrent: int = 3,
    ) -> dict[str, DocumentIntelligenceResult]:
        """Analyze multiple documents concurrently.

        Args:
            documents: List of (document_id, document_bytes) tuples.
            model_id: The model to use.
            locale: Locale hint for OCR.
            max_concurrent: Maximum concurrent analysis operations.

        Returns:
            A mapping of document_id to :class:`DocumentIntelligenceResult`.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        results: dict[str, DocumentIntelligenceResult] = {}

        async def _analyze_one(doc_id: str, doc_bytes: bytes) -> None:
            async with semaphore:
                try:
                    result = await self.analyze(doc_bytes, model_id=model_id, locale=locale)
                    results[doc_id] = result
                except Exception:
                    logger.exception("batch_analyze_failed", document_id=doc_id)

        tasks = [_analyze_one(doc_id, doc_bytes) for doc_id, doc_bytes in documents]
        await asyncio.gather(*tasks)

        logger.info("batch_analyze_complete", total=len(documents), succeeded=len(results))
        return results
