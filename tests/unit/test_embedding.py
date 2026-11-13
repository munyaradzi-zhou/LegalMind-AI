"""Unit tests for embedding generation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from legalmind.ingestion.chunking import Chunk
from legalmind.ingestion.embedding import EmbeddingGenerator


@pytest.fixture
def mock_openai():
    with patch("legalmind.ingestion.embedding.AzureOpenAIService") as mock:
        service = AsyncMock()
        service.generate_embeddings = AsyncMock(return_value=[[0.1] * 3072, [0.2] * 3072])
        service.generate_single_embedding = AsyncMock(return_value=[0.1] * 3072)
        mock.return_value = service
        yield service


def _make_chunk(content: str = "Test text", index: int = 0) -> Chunk:
    """Helper to create a Chunk for testing."""
    return Chunk(
        chunk_id=f"test-chunk-{index}",
        document_id="test-doc",
        content=content,
        chunk_index=index,
    )


class TestEmbeddingGenerator:
    """Tests for batch embedding generation."""

    @pytest.mark.asyncio
    async def test_generate_single_embedding(self, mock_openai):
        generator = EmbeddingGenerator(openai_service=mock_openai)
        chunk = _make_chunk()
        result = await generator.embed_single(chunk)
        assert len(result.embedding) == 3072

    @pytest.mark.asyncio
    async def test_generate_batch_embeddings(self, mock_openai):
        generator = EmbeddingGenerator(openai_service=mock_openai)
        chunks = [_make_chunk("Text one", 0), _make_chunk("Text two", 1)]
        results = await generator.embed_chunks(chunks)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_empty_batch(self, mock_openai):
        generator = EmbeddingGenerator(openai_service=mock_openai)
        results = await generator.embed_chunks([])
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_texts(self, mock_openai):
        """Test raw text embedding."""
        generator = EmbeddingGenerator(openai_service=mock_openai)
        texts = ["Text one", "Text two"]
        results = await generator.embed_texts(texts)
        assert len(results) == 2
        assert all(len(emb) == 3072 for emb in results)
