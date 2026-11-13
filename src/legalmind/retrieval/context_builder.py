"""Context builder — assembles search results into a cited, token-budgeted prompt context.

Takes a list of :class:`SearchResult` objects and produces a formatted context
string with citation markers (``[1]``, ``[2]``, ...) plus a companion citation
list that maps each marker back to its source document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
import tiktoken

if TYPE_CHECKING:
    from legalmind.retrieval.hybrid_search import SearchResult

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Citation:
    """A single citation entry linking a marker number to its source.

    Attributes:
        number: The citation marker number (1-indexed).
        document_id: Unique identifier of the source document.
        title: Human-readable title.
        document_type: Type of legal document.
        page_number: Page within the source document (if available).
        section_title: Section heading (if available).
        source_url: URL to the original blob or document (if available).
        chunk_id: The search index chunk id.
    """

    number: int
    document_id: str
    title: str
    document_type: str
    page_number: int | None = None
    section_title: str | None = None
    source_url: str | None = None
    chunk_id: str = ""


@dataclass(slots=True)
class ContextResult:
    """The output of the context builder.

    Attributes:
        context: The assembled context string ready for injection into a
            system or user prompt.
        citations: Ordered list of citations corresponding to the ``[N]``
            markers in the context.
        total_tokens: Approximate token count of the context string.
        chunks_included: Number of chunks that fit within the token budget.
        chunks_dropped: Number of chunks excluded due to budget or dedup.
    """

    context: str
    citations: list[Citation] = field(default_factory=list)
    total_tokens: int = 0
    chunks_included: int = 0
    chunks_dropped: int = 0


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

_CONTEXT_HEADER = (
    "Below are excerpts from Dutch legal documents relevant to the query.  "
    "Each excerpt is preceded by a citation marker [N].  Use these citations "
    "when referencing information from the sources.\n\n"
)

# Separator between chunks in the assembled context
_CHUNK_SEPARATOR = "\n\n---\n\n"


class ContextBuilder:
    """Builds a prompt-ready context string from search results.

    Features:

    * **Citation markers** — every included chunk is tagged ``[N]``.
    * **Deduplication** — overlapping chunks (same document, adjacent pages)
      are merged so the context is not repetitive.
    * **Token budget** — the builder stops adding chunks once the budget is
      exhausted, ensuring the context fits within the model's window.

    Usage::

        builder = ContextBuilder(max_tokens=6000)
        result = builder.build(search_results)
        # result.context  -> "Below are excerpts ... [1] ... [2] ..."
        # result.citations -> [Citation(number=1, ...), Citation(number=2, ...)]
    """

    def __init__(
        self,
        *,
        max_tokens: int = 6000,
        encoding_name: str = "cl100k_base",
        include_metadata_header: bool = True,
    ) -> None:
        """
        Args:
            max_tokens: Maximum token budget for the assembled context.
            encoding_name: The tiktoken encoding to use for counting tokens.
                ``cl100k_base`` is the encoding for GPT-4o and
                text-embedding-3-large.
            include_metadata_header: Whether to prepend a per-chunk header
                with title, type, and page information.
        """
        self._max_tokens = max_tokens
        self._encoding = tiktoken.get_encoding(encoding_name)
        self._include_metadata_header = include_metadata_header

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _count_tokens(self, text: str) -> int:
        """Return the number of tokens in *text*."""
        return len(self._encoding.encode(text))

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(results: list[SearchResult]) -> list[SearchResult]:
        """Remove duplicate or near-duplicate chunks.

        Two chunks are considered duplicates when they share the same
        ``document_id`` and ``chunk_index``.  Only the first (highest-ranked)
        occurrence is kept.

        Args:
            results: Relevance-sorted search results.

        Returns:
            Deduplicated list preserving the original order.
        """
        seen: set[str] = set()
        unique: list[SearchResult] = []

        for result in results:
            key = f"{result.document_id}::{result.chunk_index}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(result)

        return unique

    # ------------------------------------------------------------------
    # Chunk formatting
    # ------------------------------------------------------------------

    def _format_chunk(self, result: SearchResult, citation_number: int) -> str:
        """Format a single search result as a cited context chunk.

        Args:
            result: The search result to format.
            citation_number: The ``[N]`` marker to prepend.

        Returns:
            Formatted chunk string.
        """
        parts: list[str] = [f"[{citation_number}]"]

        if self._include_metadata_header:
            meta_parts: list[str] = []
            if result.title:
                meta_parts.append(f"Title: {result.title}")
            if result.document_type:
                meta_parts.append(f"Type: {result.document_type}")
            if result.page_number is not None:
                meta_parts.append(f"Page: {result.page_number}")
            if result.section_title:
                meta_parts.append(f"Section: {result.section_title}")

            if meta_parts:
                parts.append(" | ".join(meta_parts))

        parts.append(result.content)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, results: list[SearchResult]) -> ContextResult:
        """Assemble search results into a formatted, cited context string.

        Iterates through the (deduplicated, relevance-sorted) results,
        formatting each chunk with a ``[N]`` citation marker, and appending
        it to the context until the token budget is exhausted.

        Args:
            results: Search results ordered by relevance (highest first).

        Returns:
            A ``ContextResult`` containing the context string, the citation
            list, and usage statistics.
        """
        if not results:
            logger.info("context_builder_empty", reason="no results provided")
            return ContextResult(context="", total_tokens=0)

        unique_results = self._deduplicate(results)
        dropped_by_dedup = len(results) - len(unique_results)

        logger.info(
            "context_builder_start",
            input_count=len(results),
            unique_count=len(unique_results),
            max_tokens=self._max_tokens,
        )

        # Reserve tokens for the header
        header_tokens = self._count_tokens(_CONTEXT_HEADER)
        separator_tokens = self._count_tokens(_CHUNK_SEPARATOR)
        remaining_tokens = self._max_tokens - header_tokens

        chunks: list[str] = []
        citations: list[Citation] = []
        citation_number = 0
        dropped_by_budget = 0

        for result in unique_results:
            citation_number += 1
            formatted = self._format_chunk(result, citation_number)
            chunk_tokens = self._count_tokens(formatted)

            # Account for separator between chunks (except the first)
            extra = separator_tokens if chunks else 0

            if chunk_tokens + extra > remaining_tokens:
                dropped_by_budget += 1
                citation_number -= 1  # Roll back — this chunk was not added
                continue

            chunks.append(formatted)
            remaining_tokens -= chunk_tokens + extra

            citations.append(
                Citation(
                    number=citation_number,
                    document_id=result.document_id,
                    title=result.title,
                    document_type=result.document_type,
                    page_number=result.page_number,
                    section_title=result.section_title,
                    source_url=result.source_url,
                    chunk_id=result.id,
                )
            )

        context = _CONTEXT_HEADER + _CHUNK_SEPARATOR.join(chunks) if chunks else ""
        total_tokens = self._count_tokens(context) if context else 0

        logger.info(
            "context_builder_complete",
            chunks_included=len(chunks),
            chunks_dropped_dedup=dropped_by_dedup,
            chunks_dropped_budget=dropped_by_budget,
            total_tokens=total_tokens,
        )

        return ContextResult(
            context=context,
            citations=citations,
            total_tokens=total_tokens,
            chunks_included=len(chunks),
            chunks_dropped=dropped_by_dedup + dropped_by_budget,
        )

    def build_citation_footer(self, citations: list[Citation]) -> str:
        """Format citations as a human-readable footer.

        Produces a numbered list suitable for appending to an assistant
        response::

            Sources:
            [1] Title — contract, p. 5
            [2] Title — case_file, Section 3.2

        Args:
            citations: The citation list from a ``ContextResult``.

        Returns:
            A formatted citation footer string.
        """
        if not citations:
            return ""

        lines: list[str] = ["Sources:"]
        for c in citations:
            parts: list[str] = [f"[{c.number}] {c.title}"]
            if c.document_type:
                parts.append(c.document_type)
            if c.page_number is not None:
                parts.append(f"p. {c.page_number}")
            if c.section_title:
                parts.append(c.section_title)
            lines.append(" — ".join(parts))

        return "\n".join(lines)
