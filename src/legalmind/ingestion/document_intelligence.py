"""Azure Document Intelligence wrapper for legal document layout extraction.

Uses the ``azure-ai-documentintelligence`` SDK (API 2024-11-30) with the
``prebuilt-layout`` model to extract text, tables, key-value pairs and structure
from scanned or digital legal documents (PDF, DOCX, TIFF, images).

The content comes back as **Markdown**, so headings and tables reach the chunker
as structure instead of flat lines. :func:`normalize_markdown` turns the service's
HTML tables into Markdown tables and removes its page comments, and keeps every
page's character range valid so chunks get the right page numbers.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import structlog
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeDocumentRequest,
    DocumentAnalysisFeature,
    DocumentContentFormat,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

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
    # Character range of this page in the service's raw content: (offset, end)
    span: tuple[int, int] | None = None


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

        return _grid_to_markdown(grid)


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
        content: The full extracted text of the document (normalized Markdown).
        pages: Per-page extracted content.
        tables: Extracted tables.
        key_value_pairs: Extracted key-value pairs.
        paragraphs: Extracted paragraphs with roles (title, sectionHeading, etc.).
        page_count: Total number of pages.
        page_boundaries: ``(page_number, start, end)`` character ranges in ``content``.
        content_format: ``"markdown"`` or ``"text"``.
        mean_word_confidence: Average OCR confidence over all words (1.0 for born-digital text).
    """

    content: str
    pages: list[PageContent]
    tables: list[TableContent]
    key_value_pairs: list[KeyValuePair]
    paragraphs: list[dict[str, Any]]
    page_count: int
    page_boundaries: list[tuple[int, int, int]] = field(default_factory=list)
    content_format: str = "markdown"
    mean_word_confidence: float | None = None


# ─── Markdown normalization ──────────────────────────────────────────────────

# What the service puts in its Markdown that we don't want in chunks:
# HTML comments (<!-- PageBreak -->, <!-- PageNumber="3" -->, <!-- PageHeader=... -->),
# HTML tables, figure wrappers, and runs of blank lines.
_MARKUP = re.compile(
    r"<!--.*?-->|<table>.*?</table>|</?figure>|</?figcaption>",
    re.DOTALL | re.IGNORECASE,
)
_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


class _TableParser(HTMLParser):
    """Collect the cell texts of one HTML table, honouring colspan."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
            span = dict(attrs).get("colspan") or "1"
            self._colspan = int(span) if span.isdigit() else 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            if not self.rows:
                self.rows.append([])
            text = " ".join("".join(self._cell).split())
            self.rows[-1].append(text)
            self.rows[-1].extend("" for _ in range(self._colspan - 1))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _grid_to_markdown(grid: list[list[str]]) -> str:
    width = max((len(row) for row in grid), default=0)
    if width == 0:
        return ""
    rows = [[cell.replace("|", "\\|") for cell in row] + [""] * (width - len(row)) for row in grid]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def html_table_to_markdown(table_html: str) -> str:
    """Convert one ``<table>…</table>`` element to a Markdown table."""
    parser = _TableParser()
    parser.feed(table_html)
    parser.close()
    rows = [row for row in parser.rows if any(cell for cell in row)]
    return _grid_to_markdown(rows) if rows else ""


def _markup_replacement(markup: str) -> str:
    if markup[:6].lower() == "<table":
        return "\n" + html_table_to_markdown(markup) + "\n"
    return ""


def normalize_markdown(
    content: str,
    page_spans: list[tuple[int, int, int]],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Clean the service's Markdown and move the page ranges along with the text.

    Args:
        content: Markdown content as returned by the service.
        page_spans: ``(page_number, start, end)`` character ranges in ``content``.

    Returns:
        The cleaned text and the page ranges mapped onto it.
    """
    text, spans = _rewrite(content, page_spans, _MARKUP, _markup_replacement)
    return _rewrite(text, spans, _BLANK_LINES, lambda _: "\n\n")


def _rewrite(
    content: str,
    page_spans: list[tuple[int, int, int]],
    pattern: re.Pattern[str],
    replace: Callable[[str], str],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Replace every match of ``pattern`` and map the page ranges onto the new text."""
    parts: list[str] = []
    # Anchors (original start, original end, new start, new end) of every replaced region
    anchors: list[tuple[int, int, int, int]] = []
    position = 0
    new_length = 0
    for match in pattern.finditer(content):
        kept = content[position : match.start()]
        parts.append(kept)
        new_length += len(kept)
        replacement = replace(match.group(0))
        anchors.append((match.start(), match.end(), new_length, new_length + len(replacement)))
        parts.append(replacement)
        new_length += len(replacement)
        position = match.end()
    parts.append(content[position:])
    text = "".join(parts)

    starts = [a[0] for a in anchors]

    def mapped(offset: int) -> int:
        i = bisect.bisect_right(starts, offset) - 1
        if i < 0:
            return offset
        orig_start, orig_end, new_start, new_end = anchors[i]
        if offset < orig_end:
            # Inside a replaced region: snap to its start (or its end for a range end)
            return new_start if offset == orig_start else new_end
        return new_end + (offset - orig_end)

    boundaries = [(page, mapped(start), mapped(end)) for page, start, end in page_spans]
    return text, boundaries


# ─── Analyzer ────────────────────────────────────────────────────────────────


class DocumentIntelligenceAnalyzer:
    """Wrapper around Azure Document Intelligence for layout extraction.

    Uses the ``prebuilt-layout`` model by default, which extracts:
    - Text and reading order, as Markdown (headings, tables, lists)
    - Tables with cell content and structure
    - Selection marks (checkboxes)
    - Key-value pairs (optional feature, no extra charge)
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

    def _get_client(self) -> DocumentIntelligenceClient:
        """Create a new async Document Intelligence client."""
        return DocumentIntelligenceClient(
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
        markdown: bool = True,
    ) -> DocumentIntelligenceResult:
        """Analyze a document using Azure Document Intelligence.

        Args:
            document_bytes: Raw document bytes (PDF, DOCX, TIFF, JPEG, PNG, BMP).
            model_id: The model to use. Defaults to ``prebuilt-layout``.
            locale: Locale hint for OCR. Defaults to Dutch (``nl-NL``).
            markdown: Ask for Markdown content (layout model only).

        Returns:
            A :class:`DocumentIntelligenceResult` with all extracted content.

        Raises:
            azure.core.exceptions.HttpResponseError: On API errors.
        """
        use_markdown = markdown and model_id == "prebuilt-layout"
        logger.info(
            "document_intelligence_analyze_start",
            model_id=model_id,
            locale=locale,
            size_bytes=len(document_bytes),
            markdown=use_markdown,
        )

        async with self._get_client() as client:
            poller = await client.begin_analyze_document(
                model_id,
                AnalyzeDocumentRequest(bytes_source=document_bytes),
                locale=locale,
                # Offsets in Python string indices, so page ranges can slice `content` directly
                string_index_type="unicodeCodePoint",
                output_content_format=DocumentContentFormat.MARKDOWN if use_markdown else DocumentContentFormat.TEXT,
                features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS] if model_id == "prebuilt-layout" else None,
            )
            result = await poller.result()

        doc_result = self.build_result(result, markdown=use_markdown)

        logger.info(
            "document_intelligence_analyze_complete",
            page_count=doc_result.page_count,
            table_count=len(doc_result.tables),
            kv_pair_count=len(doc_result.key_value_pairs),
            paragraph_count=len(doc_result.paragraphs),
            mean_word_confidence=doc_result.mean_word_confidence,
        )

        return doc_result

    @classmethod
    def build_result(cls, result: Any, *, markdown: bool = True) -> DocumentIntelligenceResult:
        """Turn the service's ``AnalyzeResult`` into a :class:`DocumentIntelligenceResult`."""
        raw_content = result.content or ""
        pages = cls._extract_pages(result)
        page_spans = [(p.page_number, p.span[0], p.span[1]) for p in pages if p.span is not None]
        if markdown:
            content, boundaries = normalize_markdown(raw_content, page_spans)
        else:
            content, boundaries = raw_content, page_spans

        confidences = [w["confidence"] for p in pages for w in p.words if w.get("confidence") is not None]

        return DocumentIntelligenceResult(
            content=content,
            pages=pages,
            tables=cls._extract_tables(result),
            key_value_pairs=cls._extract_key_value_pairs(result),
            paragraphs=cls._extract_paragraphs(result),
            page_count=len(pages),
            page_boundaries=boundaries,
            content_format="markdown" if markdown else "text",
            mean_word_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        )

    @staticmethod
    def _extract_pages(result: Any) -> list[PageContent]:
        """Extract per-page content from the analysis result."""
        pages: list[PageContent] = []

        for page in result.pages or []:
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

            spans = page.spans or []
            span = (min(s.offset for s in spans), max(s.offset + s.length for s in spans)) if spans else None

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
                    span=span,
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
