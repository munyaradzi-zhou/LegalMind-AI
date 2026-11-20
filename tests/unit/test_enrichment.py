"""Tests for chunk enrichment."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from legalmind.ingestion.enrichment import ChunkEnricher


@pytest.mark.asyncio
async def test_dutch_is_not_sent_to_summarization() -> None:
    client = MagicMock()
    client.summarize_text = AsyncMock(return_value=["samenvatting"])

    summaries = await ChunkEnricher(client)._safe_summarize(["Artikel 1", "Artikel 2"], "nl")

    assert summaries == ["", ""]
    client.summarize_text.assert_not_called()


@pytest.mark.asyncio
async def test_supported_language_is_summarized() -> None:
    client = MagicMock()
    client.summarize_text = AsyncMock(return_value=["summary"])

    assert await ChunkEnricher(client)._safe_summarize(["Article 1"], "en") == ["summary"]
