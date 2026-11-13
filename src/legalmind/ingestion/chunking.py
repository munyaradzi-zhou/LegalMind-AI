"""Document chunking strategies for legal documents.

Provides three chunking approaches for creating search-optimized text
segments from extracted legal document content:

- **Semantic chunking** — splits by document structure (headings, paragraphs).
- **Fixed-size chunking** — splits by character count with configurable overlap.
- **Parent-child chunking** — creates a two-level hierarchy for retrieval.

Each chunk is augmented with metadata (page number, section title, chunk index,
parent ID) to support precise source attribution in retrieval results.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A single document chunk with metadata.

    Attributes:
        chunk_id: Unique identifier for this chunk.
        document_id: ID of the source document.
        content: The text content of the chunk.
        chunk_index: Zero-based index of this chunk within the document.
        page_number: Primary page number where this chunk begins.
        page_numbers: All page numbers spanned by this chunk.
        section_title: Title of the section this chunk belongs to.
        parent_id: ID of the parent chunk (for child chunks in hierarchical retrieval).
        chunk_type: Type of chunk — ``"chunk"``, ``"parent"``, or ``"child"``.
        char_count: Number of characters in the content.
        metadata: Additional metadata attached to the chunk.
    """

    chunk_id: str
    document_id: str
    content: str
    chunk_index: int
    page_number: int = 1
    page_numbers: list[int] = field(default_factory=list)
    section_title: str = ""
    parent_id: str | None = None
    chunk_type: str = "chunk"
    char_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.char_count = len(self.content)
        if not self.page_numbers:
            self.page_numbers = [self.page_number]


# ─── Heading / section detection ─────────────────────────────────────────────

# Patterns for detecting section headings in Dutch legal documents.
# Matches patterns like: "Artikel 1.", "1. Definities", "HOOFDSTUK II", "Paragraaf 3.2"
_HEADING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s+"),  # Markdown headings
    re.compile(r"^(?:Artikel|Art\.?)\s+\d+", re.IGNORECASE),
    re.compile(r"^(?:HOOFDSTUK|Hoofdstuk)\s+[IVXLCDM\d]+", re.IGNORECASE),
    re.compile(r"^(?:Paragraaf|Par\.?)\s+\d+", re.IGNORECASE),
    re.compile(r"^(?:Afdeling)\s+\d+", re.IGNORECASE),
    re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z]"),  # "1. Definities", "3.2 Verplichtingen"
    re.compile(r"^[A-Z][A-Z\s]{2,}$"),  # ALL CAPS lines (common headings)
]


def _is_heading(line: str) -> bool:
    """Check whether a line looks like a section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 200:
        return False
    return any(pattern.match(stripped) for pattern in _HEADING_PATTERNS)


def _extract_heading_title(line: str) -> str:
    """Extract a clean heading title from a line."""
    return line.strip().rstrip(".")


# ─── Page mapping helpers ────────────────────────────────────────────────────


def _resolve_page_number(
    char_offset: int,
    page_boundaries: list[tuple[int, int, int]],
) -> int:
    """Given a character offset, determine which page it falls on.

    Args:
        char_offset: Character offset into the full document text.
        page_boundaries: List of (page_number, start_offset, end_offset).

    Returns:
        The page number, defaulting to 1.
    """
    for page_num, start, end in page_boundaries:
        if start <= char_offset < end:
            return page_num
    return 1


def _resolve_page_numbers(
    start_offset: int,
    end_offset: int,
    page_boundaries: list[tuple[int, int, int]],
) -> list[int]:
    """Determine all page numbers spanned by a character range.

    Args:
        start_offset: Start character offset.
        end_offset: End character offset.
        page_boundaries: List of (page_number, start_offset, end_offset).

    Returns:
        Sorted list of page numbers.
    """
    pages: set[int] = set()
    for page_num, p_start, p_end in page_boundaries:
        if p_start < end_offset and p_end > start_offset:
            pages.add(page_num)
    return sorted(pages) if pages else [1]


def build_page_boundaries(paragraphs: list[dict[str, Any]], full_text: str) -> list[tuple[int, int, int]]:
    """Build page boundary mappings from paragraph metadata.

    Uses paragraph page numbers to estimate where each page begins and ends
    within the full document text.

    Args:
        paragraphs: List of paragraph dicts with ``content`` and ``page_numbers``.
        full_text: The complete document text.

    Returns:
        List of (page_number, start_offset, end_offset) tuples.
    """
    if not paragraphs:
        return [(1, 0, len(full_text))]

    page_starts: dict[int, int] = {}
    page_ends: dict[int, int] = {}
    offset = 0

    for para in paragraphs:
        content = para.get("content", "")
        p_start = full_text.find(content, offset)
        if p_start == -1:
            p_start = offset
        p_end = p_start + len(content)

        for page_num in para.get("page_numbers", [1]):
            if page_num not in page_starts:
                page_starts[page_num] = p_start
            page_ends[page_num] = max(page_ends.get(page_num, 0), p_end)

        offset = max(offset, p_start + 1)

    boundaries: list[tuple[int, int, int]] = []
    for page_num in sorted(page_starts):
        boundaries.append((page_num, page_starts[page_num], page_ends[page_num]))

    return boundaries if boundaries else [(1, 0, len(full_text))]


# ─── Semantic Chunking ───────────────────────────────────────────────────────


def semantic_chunk(
    full_text: str,
    document_id: str,
    *,
    paragraphs: list[dict[str, Any]] | None = None,
    min_chunk_size: int = 200,
    max_chunk_size: int = 2000,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Split document text by semantic sections (headings and paragraphs).

    Respects document structure — never splits mid-paragraph. Short sections
    are merged with the following section to avoid tiny chunks.

    Args:
        full_text: Complete document text.
        document_id: ID of the source document.
        paragraphs: Optional paragraph metadata from Document Intelligence
            (used for accurate page number mapping).
        min_chunk_size: Minimum characters per chunk. Shorter sections are merged.
        max_chunk_size: Maximum characters per chunk. Sections exceeding this
            are split into fixed-size sub-chunks.
        metadata: Additional metadata to attach to every chunk.

    Returns:
        List of :class:`Chunk` objects.
    """
    if not full_text.strip():
        return []

    page_boundaries = build_page_boundaries(paragraphs or [], full_text)
    extra_meta = metadata or {}

    # Split the text into sections based on headings
    sections = _split_into_sections(full_text)

    chunks: list[Chunk] = []
    chunk_index = 0

    accumulated_text = ""
    accumulated_title = ""
    accumulated_start_offset = 0

    for title, body, start_offset in sections:
        section_text = f"{title}\n\n{body}".strip() if title else body.strip()

        # If accumulated text is below minimum, keep accumulating
        if accumulated_text and len(accumulated_text) + len(section_text) < min_chunk_size:
            accumulated_text += "\n\n" + section_text
            continue

        # Flush accumulated text as a chunk
        if accumulated_text:
            chunks.extend(
                _create_chunks_from_section(
                    text=accumulated_text,
                    document_id=document_id,
                    section_title=accumulated_title,
                    start_offset=accumulated_start_offset,
                    start_index=chunk_index,
                    max_chunk_size=max_chunk_size,
                    page_boundaries=page_boundaries,
                    extra_metadata=extra_meta,
                )
            )
            chunk_index = len(chunks)

        accumulated_text = section_text
        accumulated_title = title
        accumulated_start_offset = start_offset

    # Flush the last accumulated section
    if accumulated_text:
        chunks.extend(
            _create_chunks_from_section(
                text=accumulated_text,
                document_id=document_id,
                section_title=accumulated_title,
                start_offset=accumulated_start_offset,
                start_index=chunk_index,
                max_chunk_size=max_chunk_size,
                page_boundaries=page_boundaries,
                extra_metadata=extra_meta,
            )
        )

    return chunks


def _split_into_sections(text: str) -> list[tuple[str, str, int]]:
    """Split text into (title, body, start_offset) tuples based on headings.

    Args:
        text: The full document text.

    Returns:
        List of (section_title, section_body, character_offset) tuples.
    """
    lines = text.split("\n")
    sections: list[tuple[str, str, int]] = []
    current_title = ""
    current_lines: list[str] = []
    current_offset = 0
    offset = 0

    for line in lines:
        if _is_heading(line):
            # Flush current section
            if current_lines or current_title:
                body = "\n".join(current_lines)
                sections.append((current_title, body, current_offset))

            current_title = _extract_heading_title(line)
            current_lines = []
            current_offset = offset
        else:
            current_lines.append(line)

        offset += len(line) + 1  # +1 for the newline

    # Flush final section
    if current_lines or current_title:
        body = "\n".join(current_lines)
        sections.append((current_title, body, current_offset))

    # If only one section was produced (no headings found), try splitting on
    # paragraph boundaries (double newlines) as a fallback.
    if len(sections) <= 1:
        paragraphs = re.split(r"\n\n+", text)
        if len(paragraphs) > 1:
            sections = []
            para_offset = 0
            for para in paragraphs:
                idx = text.find(para, para_offset)
                if idx == -1:
                    idx = para_offset
                sections.append(("", para, idx))
                para_offset = idx + len(para)

    if not sections:
        sections = [("", text, 0)]

    return sections


def _create_chunks_from_section(
    text: str,
    document_id: str,
    section_title: str,
    start_offset: int,
    start_index: int,
    max_chunk_size: int,
    page_boundaries: list[tuple[int, int, int]],
    extra_metadata: dict[str, Any],
) -> list[Chunk]:
    """Create one or more chunks from a section, splitting if it exceeds max size.

    Args:
        text: The section text.
        document_id: Source document ID.
        section_title: Title of the section.
        start_offset: Character offset of the section in the full document.
        start_index: Starting chunk index.
        max_chunk_size: Maximum chunk size in characters.
        page_boundaries: Page boundary data.
        extra_metadata: Extra metadata to attach.

    Returns:
        List of Chunk objects.
    """
    if len(text) <= max_chunk_size:
        page_nums = _resolve_page_numbers(start_offset, start_offset + len(text), page_boundaries)
        return [
            Chunk(
                chunk_id=str(uuid.uuid4()),
                document_id=document_id,
                content=text,
                chunk_index=start_index,
                page_number=page_nums[0] if page_nums else 1,
                page_numbers=page_nums,
                section_title=section_title,
                chunk_type="chunk",
                metadata=extra_metadata,
            )
        ]

    # Section is too large — use fixed-size splitting within the section
    sub_chunks = fixed_size_chunk(
        text,
        document_id,
        chunk_size=max_chunk_size,
        overlap=200,
        metadata=extra_metadata,
    )

    # Re-number and add section context
    result: list[Chunk] = []
    for i, chunk in enumerate(sub_chunks):
        offset = start_offset + text.find(chunk.content[:100])
        page_nums = _resolve_page_numbers(offset, offset + len(chunk.content), page_boundaries)
        result.append(
            Chunk(
                chunk_id=str(uuid.uuid4()),
                document_id=document_id,
                content=chunk.content,
                chunk_index=start_index + i,
                page_number=page_nums[0] if page_nums else 1,
                page_numbers=page_nums,
                section_title=section_title,
                chunk_type="chunk",
                metadata=extra_metadata,
            )
        )

    return result


# ─── Fixed-Size Chunking ─────────────────────────────────────────────────────


def fixed_size_chunk(
    full_text: str,
    document_id: str,
    *,
    chunk_size: int = 1500,
    overlap: int = 200,
    page_boundaries: list[tuple[int, int, int]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Split text into fixed-size chunks with overlap.

    Tries to break at sentence boundaries when possible to avoid cutting
    mid-sentence. Falls back to word boundaries, then character boundaries.

    Args:
        full_text: Complete document text.
        document_id: ID of the source document.
        chunk_size: Target number of characters per chunk.
        overlap: Number of overlapping characters between consecutive chunks.
        page_boundaries: Optional page boundary data for page number resolution.
        metadata: Additional metadata to attach to every chunk.

    Returns:
        List of :class:`Chunk` objects.
    """
    if not full_text.strip():
        return []

    extra_meta = metadata or {}
    boundaries = page_boundaries or [(1, 0, len(full_text))]

    # Sentence-ending pattern for Dutch legal text
    sentence_end = re.compile(r"[.;:!?]\s+")

    chunks: list[Chunk] = []
    start = 0
    chunk_index = 0

    while start < len(full_text):
        end = min(start + chunk_size, len(full_text))

        # If not at the end of the document, try to find a clean break point
        if end < len(full_text):
            # Look for the last sentence boundary within the chunk window
            search_region = full_text[start:end]
            matches = list(sentence_end.finditer(search_region))
            if matches:
                # Use the last sentence boundary found
                last_match = matches[-1]
                end = start + last_match.end()
            else:
                # Fall back to word boundary
                space_idx = full_text.rfind(" ", start, end)
                if space_idx > start:
                    end = space_idx + 1

        chunk_text = full_text[start:end].strip()
        if chunk_text:
            page_nums = _resolve_page_numbers(start, end, boundaries)
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    document_id=document_id,
                    content=chunk_text,
                    chunk_index=chunk_index,
                    page_number=page_nums[0] if page_nums else 1,
                    page_numbers=page_nums,
                    chunk_type="chunk",
                    metadata=extra_meta,
                )
            )
            chunk_index += 1

        # Advance position — stop if we've reached the end of the text
        if end >= len(full_text):
            break
        start = max(start + 1, end - overlap)

    return chunks


# ─── Parent-Child Chunking ───────────────────────────────────────────────────


def parent_child_chunk(
    full_text: str,
    document_id: str,
    *,
    paragraphs: list[dict[str, Any]] | None = None,
    parent_max_size: int = 4000,
    child_size: int = 800,
    child_overlap: int = 100,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Create a two-level hierarchy of parent and child chunks.

    **Parent chunks** correspond to full document sections (or large segments).
    They provide broad context for the LLM when generating answers.

    **Child chunks** are smaller sub-segments of parent chunks. They are used
    for fine-grained retrieval matching. Each child chunk stores a ``parent_id``
    reference so the retrieval system can fetch the broader parent context.

    Args:
        full_text: Complete document text.
        document_id: ID of the source document.
        paragraphs: Optional paragraph metadata for page mapping.
        parent_max_size: Maximum size of parent chunks in characters.
        child_size: Target size of child chunks in characters.
        child_overlap: Overlap between consecutive child chunks.
        metadata: Additional metadata to attach to every chunk.

    Returns:
        List of :class:`Chunk` objects (parents and children interleaved).
        Parent chunks have ``chunk_type="parent"``, children have
        ``chunk_type="child"`` and a populated ``parent_id``.
    """
    if not full_text.strip():
        return []

    extra_meta = metadata or {}
    page_boundaries = build_page_boundaries(paragraphs or [], full_text)

    # First, create semantic sections as parent chunks
    sections = _split_into_sections(full_text)

    all_chunks: list[Chunk] = []
    chunk_index = 0

    for title, body, section_offset in sections:
        section_text = f"{title}\n\n{body}".strip() if title else body.strip()
        if not section_text:
            continue

        # If section exceeds parent_max_size, split it into multiple parents
        parent_texts = _split_oversized(section_text, parent_max_size)

        for parent_text in parent_texts:
            parent_offset = section_offset + section_text.find(parent_text[:100])
            parent_page_nums = _resolve_page_numbers(
                parent_offset,
                parent_offset + len(parent_text),
                page_boundaries,
            )

            parent_id = str(uuid.uuid4())
            parent_chunk = Chunk(
                chunk_id=parent_id,
                document_id=document_id,
                content=parent_text,
                chunk_index=chunk_index,
                page_number=parent_page_nums[0] if parent_page_nums else 1,
                page_numbers=parent_page_nums,
                section_title=title,
                chunk_type="parent",
                metadata=extra_meta,
            )
            all_chunks.append(parent_chunk)
            chunk_index += 1

            # Create child chunks from this parent
            child_chunks = fixed_size_chunk(
                parent_text,
                document_id,
                chunk_size=child_size,
                overlap=child_overlap,
                page_boundaries=page_boundaries,
                metadata=extra_meta,
            )

            for child in child_chunks:
                child.parent_id = parent_id
                child.chunk_type = "child"
                child.section_title = title
                child.chunk_index = chunk_index
                all_chunks.append(child)
                chunk_index += 1

    return all_chunks


def _split_oversized(text: str, max_size: int) -> list[str]:
    """Split text that exceeds max_size into roughly equal parts at paragraph breaks.

    Args:
        text: The text to split.
        max_size: Maximum size per segment.

    Returns:
        List of text segments, each no larger than max_size (approximately).
    """
    if len(text) <= max_size:
        return [text]

    paragraphs = text.split("\n\n")
    segments: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the \n\n separator
        if current_len + para_len > max_size and current:
            segments.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += para_len

    if current:
        segments.append("\n\n".join(current))

    return segments
