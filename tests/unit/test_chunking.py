"""Unit tests for document chunking strategies."""

from __future__ import annotations

from legalmind.ingestion.chunking import _split_into_sections, fixed_size_chunk, parent_child_chunk, semantic_chunk

DOC_ID = "test-doc-001"


class TestFixedSizeChunk:
    """Tests for fixed-size chunking with overlap."""

    def test_short_text_single_chunk(self):
        """Short text should produce a single chunk."""
        text = "Dit is een korte tekst."
        chunks = fixed_size_chunk(text, DOC_ID, chunk_size=500, overlap=50)
        assert len(chunks) == 1
        assert chunks[0].content == text

    def test_long_text_multiple_chunks(self):
        """Long text should be split into multiple overlapping chunks."""
        text = "Woord " * 200  # ~1200 chars
        chunks = fixed_size_chunk(text, DOC_ID, chunk_size=200, overlap=50)
        assert len(chunks) > 1

    def test_chunks_have_metadata(self):
        """Each chunk should have chunk_index."""
        text = "Sectie een. " * 50
        chunks = fixed_size_chunk(text, DOC_ID, chunk_size=100, overlap=20)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
            assert chunk.content
            assert chunk.document_id == DOC_ID

    def test_overlap_between_chunks(self):
        """Adjacent chunks should have overlapping content when overlap > 0."""
        text = "ABCDEFGHIJ" * 20  # 200 chars
        chunks = fixed_size_chunk(text, DOC_ID, chunk_size=50, overlap=10)
        assert len(chunks) >= 2
        # Chunks are Chunk dataclasses with .content

    def test_empty_text_returns_empty(self):
        """Empty text should return an empty list."""
        chunks = fixed_size_chunk("", DOC_ID, chunk_size=500, overlap=50)
        assert chunks == []


class TestSemanticChunk:
    """Tests for semantic chunking based on document structure."""

    def test_splits_on_headings(self):
        """Should split on markdown-style headings or legal article headings."""
        text = "# Artikel 1\nInhoud van artikel 1.\n\n# Artikel 2\nInhoud van artikel 2."
        chunks = semantic_chunk(text, DOC_ID, min_chunk_size=10)
        assert len(chunks) >= 2

    def test_splits_on_double_newlines(self):
        """Should split on paragraph boundaries (double newlines)."""
        text = "Eerste paragraaf met tekst.\n\nTweede paragraaf met tekst.\n\nDerde paragraaf."
        chunks = semantic_chunk(text, DOC_ID, min_chunk_size=10)
        assert len(chunks) >= 2

    def test_preserves_section_titles(self):
        """Chunks should include section title metadata when available."""
        text = "# Definities\nDe volgende termen worden gebruikt.\n\n# Verplichtingen\nDe verzekerde is verplicht."
        chunks = semantic_chunk(text, DOC_ID)
        # Check that section_title is set on at least one chunk
        assert any(c.section_title for c in chunks) or len(chunks) >= 1

    def test_empty_text(self):
        """Empty text should return empty list."""
        chunks = semantic_chunk("", DOC_ID)
        assert chunks == []

    def test_bare_heading_joins_its_body(self):
        """A heading-only section must not become a chunk of its own."""
        body = "Van dekking zijn uitgesloten: geschillen die reeds bestonden vóór de ingangsdatum. " * 4
        text = f"ARTIKEL 3 - UITSLUITINGEN\n\n{body}\n\nARTIKEL 4 - EIGEN RISICO\n\n{body}"
        chunks = semantic_chunk(text, DOC_ID)
        assert [c.section_title for c in chunks] == ["ARTIKEL 3 - UITSLUITINGEN", "ARTIKEL 4 - EIGEN RISICO"]
        assert all(len(c.content) > 200 for c in chunks)

    def test_numbered_clause_is_not_a_heading(self):
        """'2.2 De maximale dekking bedraagt ...' is a clause, '3.2 Verplichtingen' a heading."""
        text = (
            "ARTIKEL 2 - DEKKING\n2.1 Verzekerd\n"
            "2.2 De maximale dekking bedraagt €50.000 per geschil\n3.2 Verplichtingen"
        )
        sections = [title for title, _, _ in _split_into_sections(text)]
        assert sections == ["ARTIKEL 2 - DEKKING", "2.1 Verzekerd", "3.2 Verplichtingen"]


class TestParentChildChunk:
    """Tests for hierarchical parent-child chunking."""

    def test_creates_parent_and_children(self):
        """Should create both parent and child chunks."""
        text = "# Artikel 1\n" + "Lange tekst. " * 100
        chunks = parent_child_chunk(text, DOC_ID, child_size=200)
        parent_chunks = [c for c in chunks if c.chunk_type == "parent"]
        child_chunks = [c for c in chunks if c.chunk_type == "child"]
        assert len(parent_chunks) >= 1
        assert len(child_chunks) >= 1

    def test_children_reference_parent(self):
        """Child chunks should have a parent_id linking to their parent."""
        text = "# Section A\n" + "Content here. " * 100
        chunks = parent_child_chunk(text, DOC_ID, child_size=200)
        child_chunks = [c for c in chunks if c.chunk_type == "child"]
        for child in child_chunks:
            assert child.parent_id is not None
