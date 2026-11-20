"""Tests for the Document Intelligence result handling (no service calls)."""

from __future__ import annotations

from types import SimpleNamespace

from legalmind.ingestion.chunking import semantic_chunk
from legalmind.ingestion.document_intelligence import (
    DocumentIntelligenceAnalyzer,
    html_table_to_markdown,
    normalize_markdown,
)

TABLE = (
    "<table><tr><th>Maand</th><th>Huur</th></tr>"
    "<tr><td>Januari</td><td>&euro; 1.250</td></tr>"
    "<tr><td colspan=2>Totaal | jaar</td></tr></table>"
)


def test_html_table_becomes_markdown() -> None:
    assert html_table_to_markdown(TABLE).splitlines() == [
        "| Maand | Huur |",
        "| --- | --- |",
        "| Januari | € 1.250 |",
        "| Totaal \\| jaar |  |",
    ]


def test_normalize_removes_page_comments_and_keeps_page_ranges() -> None:
    page1 = '# Huurovereenkomst\n\nArtikel 1\nDe huurprijs bedraagt € 1.250.\n<!-- PageNumber="1" -->\n'
    page2 = "<!-- PageBreak -->\n\n\n\nArtikel 2\n1\\. De opzegtermijn is drie maanden.\n"
    content = page1 + page2
    spans = [(1, 0, len(page1)), (2, len(page1), len(content))]

    text, boundaries = normalize_markdown(content, spans)

    assert "<!--" not in text
    assert "\n\n\n" not in text
    assert "1. De opzegtermijn" in text
    (_, s1, e1), (_, s2, e2) = boundaries
    assert "Artikel 1" in text[s1:e1] and "Artikel 2" not in text[s1:e1]
    assert "Artikel 2" in text[s2:e2] and "Artikel 1" not in text[s2:e2]
    assert e2 == len(text)


def test_normalize_converts_tables_in_place() -> None:
    before = "Het huurschema:\n\n"
    content = before + TABLE + "\n\nEinde."
    text, [(_, start, end)] = normalize_markdown(content, [(1, 0, len(content))])

    assert "<table>" not in text
    assert "| Januari | € 1.250 |" in text
    assert text.index("Het huurschema") < text.index("| Maand") < text.index("Einde.")
    assert (start, end) == (0, len(text))


def _span(offset: int, length: int) -> SimpleNamespace:
    return SimpleNamespace(offset=offset, length=length)


def _page(number: int, offset: int, length: int, confidence: float) -> SimpleNamespace:
    return SimpleNamespace(
        page_number=number,
        width=8.5,
        height=11,
        unit="inch",
        lines=[SimpleNamespace(content="regel")],
        words=[SimpleNamespace(content="woord", confidence=confidence, polygon=[])],
        selection_marks=[],
        spans=[_span(offset, length)],
    )


def test_build_result_maps_pages_and_confidence() -> None:
    page1 = "## Artikel 1 Huurprijs\n\nDe huurprijs bedraagt € 1.250 per maand.\n\n"
    page2 = "<!-- PageBreak -->\n\n## Artikel 2 Opzegging\n\nDe opzegtermijn is drie maanden.\n"
    raw = SimpleNamespace(
        content=page1 + page2,
        pages=[_page(1, 0, len(page1), 0.98), _page(2, len(page1), len(page2), 0.90)],
        tables=[],
        key_value_pairs=[],
        paragraphs=[],
    )

    result = DocumentIntelligenceAnalyzer.build_result(raw)

    assert result.content_format == "markdown"
    assert result.page_count == 2
    assert result.mean_word_confidence == 0.94
    assert "<!--" not in result.content

    chunks = semantic_chunk(
        result.content, "doc-1", page_boundaries=result.page_boundaries, min_chunk_size=10, max_chunk_size=2000
    )
    pages_by_title = {c.section_title: c.page_number for c in chunks}
    assert pages_by_title == {"## Artikel 1 Huurprijs": 1, "## Artikel 2 Opzegging": 2}
