"""Contextual chunk headers: every chunk says which document it comes from."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from legalmind.ingestion.chunking import Chunk
from legalmind.ingestion.document_context import (
    add_context_header,
    describe_document,
    strip_context_header,
)
from legalmind.ingestion.pipeline import IngestionPipeline

DESCRIPTION = (
    "Polisvoorwaarden van de rechtsbijstandverzekering van Voorbeeld Rechtsbijstand N.V., polis VP-2024-001234."
)


def _chunk(content: str) -> Chunk:
    return Chunk(chunk_id="d1_0", document_id="d1", content=content, chunk_index=0)


def test_header_is_put_above_every_chunk() -> None:
    chunks = [_chunk("ARTIKEL 6 - PREMIE\n6.1 De jaarpremie bedraagt €180,00."), _chunk("ARTIKEL 12 - OPZEGGING")]
    add_context_header(chunks, DESCRIPTION)
    assert all(c.content.startswith(f"Document: {DESCRIPTION}\n") for c in chunks)
    assert chunks[0].char_count == len(chunks[0].content)


def test_no_description_leaves_chunks_alone() -> None:
    chunks = [_chunk("ARTIKEL 6 - PREMIE")]
    add_context_header(chunks, "")
    assert chunks[0].content == "ARTIKEL 6 - PREMIE"


def test_strip_returns_the_chunks_own_text() -> None:
    chunk = _chunk("6.1 De jaarpremie bedraagt €180,00.")
    add_context_header([chunk], DESCRIPTION)
    assert strip_context_header(chunk.content) == "6.1 De jaarpremie bedraagt €180,00."
    assert strip_context_header("Geen header hier.") == "Geen header hier."


@pytest.mark.asyncio
async def test_describe_document_returns_one_line() -> None:
    openai = MagicMock()
    openai.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": f"  {DESCRIPTION}\n"}}]})
    assert await describe_document(openai, "VERZEKERINGSOVEREENKOMST ...", "polis.txt") == DESCRIPTION
    prompt = openai.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert "polis.txt" in prompt


@pytest.mark.asyncio
async def test_describe_document_failure_doesnt_break_ingestion() -> None:
    openai = MagicMock()
    openai.chat_completion = AsyncMock(side_effect=RuntimeError("429"))
    assert await describe_document(openai, "tekst", "polis.txt") == ""


@pytest.mark.asyncio
async def test_reingesting_removes_earlier_versions_by_title() -> None:
    search = MagicMock()
    search.find_ids = AsyncMock(return_value=["old_0", "old_1"])
    search.delete_documents = AsyncMock()
    pipeline = IngestionPipeline(
        blob_service=MagicMock(),
        content_understanding=MagicMock(),
        doc_intelligence=MagicMock(),
        cognitive_client=MagicMock(),
        openai_service=MagicMock(),
        search_service=search,
    )
    removed = await pipeline._remove_earlier_versions("Polis van O'Brien.txt", "new")
    assert removed == 2
    search.find_ids.assert_awaited_once_with("title eq 'Polis van O''Brien.txt' and document_id ne 'new'")
    search.delete_documents.assert_awaited_once_with(["old_0", "old_1"])
