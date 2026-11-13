"""Embedding generation for document chunks.

Uses :class:`AzureOpenAIService` to generate vector embeddings for document
chunks in configurable batches, with rate limiting and retry handling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from legalmind.services import AzureOpenAIService

if TYPE_CHECKING:
    from legalmind.ingestion.chunking import Chunk

logger = structlog.get_logger(__name__)

# Azure OpenAI embedding API limits
_DEFAULT_BATCH_SIZE = 16
_MAX_TOKENS_PER_REQUEST = 8191

# Rate limiting: minimum delay between batches (seconds)
_INTER_BATCH_DELAY = 0.1


@dataclass
class EmbeddedChunk:
    """A chunk paired with its embedding vector.

    Attributes:
        chunk: The source :class:`Chunk`.
        embedding: The vector embedding (3072 dimensions for text-embedding-3-large).
    """

    chunk: Chunk
    embedding: list[float]


class EmbeddingGenerator:
    """Generates vector embeddings for document chunks.

    Processes chunks in batches to stay within Azure OpenAI token and rate
    limits. Includes automatic retry with exponential backoff for transient
    failures and rate limit (429) responses.

    Usage::

        generator = EmbeddingGenerator(batch_size=16)
        embedded_chunks = await generator.embed_chunks(chunks)
    """

    def __init__(
        self,
        openai_service: AzureOpenAIService | None = None,
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        """Initialize the embedding generator.

        Args:
            openai_service: An existing ``AzureOpenAIService`` instance.
                If ``None``, a new one is created.
            batch_size: Number of chunks to embed per API call.
                Must be between 1 and 2048. Recommended: 16-64.
        """
        self._openai = openai_service or AzureOpenAIService()
        self._batch_size = max(1, min(batch_size, 2048))

    async def embed_chunks(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        """Generate embeddings for a list of chunks.

        Processes chunks in batches. Each batch is sent as a single API call.
        Rate limiting is applied between batches to avoid 429 errors.

        Args:
            chunks: List of chunks to embed.

        Returns:
            List of :class:`EmbeddedChunk` objects in the same order as input.

        Raises:
            openai.RateLimitError: If rate limit is exceeded after all retries.
            openai.APIError: On other API errors after all retries.
        """
        if not chunks:
            return []

        logger.info(
            "embed_chunks_start",
            chunk_count=len(chunks),
            batch_size=self._batch_size,
            total_batches=(len(chunks) + self._batch_size - 1) // self._batch_size,
        )

        embedded_chunks: list[EmbeddedChunk] = []
        total_tokens_estimated = 0

        for batch_start in range(0, len(chunks), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(chunks))
            batch = chunks[batch_start:batch_end]

            texts = [self._prepare_text(chunk) for chunk in batch]
            total_tokens_estimated += sum(self._estimate_tokens(t) for t in texts)

            embeddings = await self._generate_batch_embeddings(texts)

            for chunk, embedding in zip(batch, embeddings, strict=False):
                embedded_chunks.append(EmbeddedChunk(chunk=chunk, embedding=embedding))

            logger.debug(
                "embed_batch_complete",
                batch_start=batch_start,
                batch_size=len(batch),
                progress=f"{batch_end}/{len(chunks)}",
            )

            # Rate limiting delay between batches
            if batch_end < len(chunks):
                await asyncio.sleep(_INTER_BATCH_DELAY)

        logger.info(
            "embed_chunks_complete",
            chunk_count=len(embedded_chunks),
            estimated_tokens=total_tokens_estimated,
        )

        return embedded_chunks

    async def embed_single(self, chunk: Chunk) -> EmbeddedChunk:
        """Generate an embedding for a single chunk.

        Args:
            chunk: The chunk to embed.

        Returns:
            An :class:`EmbeddedChunk`.
        """
        text = self._prepare_text(chunk)
        embedding = await self._openai.generate_single_embedding(text)
        return EmbeddedChunk(chunk=chunk, embedding=embedding)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for raw text strings.

        Useful when embedding queries or other non-chunk text.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        for batch_start in range(0, len(texts), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(texts))
            batch = texts[batch_start:batch_end]

            embeddings = await self._generate_batch_embeddings(batch)
            all_embeddings.extend(embeddings)

            if batch_end < len(texts):
                await asyncio.sleep(_INTER_BATCH_DELAY)

        return all_embeddings

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=60),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            "embedding_retry",
            attempt=retry_state.attempt_number,
            wait=retry_state.next_action.sleep if retry_state.next_action else 0,
        ),
    )
    async def _generate_batch_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts with retry logic.

        The retry decorator handles rate limiting (429) and transient errors
        with exponential backoff.

        Args:
            texts: List of text strings (already prepared/truncated).

        Returns:
            List of embedding vectors.
        """
        return await self._openai.generate_embeddings(texts)

    @staticmethod
    def _prepare_text(chunk: Chunk) -> str:
        """Prepare chunk text for embedding.

        Prepends the section title (if available) to provide context, and
        truncates to stay within token limits.

        Args:
            chunk: The chunk to prepare text for.

        Returns:
            The prepared text string.
        """
        parts: list[str] = []

        if chunk.section_title:
            parts.append(f"[{chunk.section_title}]")

        parts.append(chunk.content)

        text = " ".join(parts)

        # Rough truncation to stay within token limits.
        # text-embedding-3-large supports 8191 tokens; ~4 chars per token is a
        # conservative estimate.
        max_chars = _MAX_TOKENS_PER_REQUEST * 4
        if len(text) > max_chars:
            text = text[:max_chars]

        return text

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate the token count for a text string.

        Uses a simple heuristic of ~4 characters per token. For precise
        counting, use ``tiktoken``, but this is sufficient for progress
        logging.

        Args:
            text: The text string.

        Returns:
            Estimated token count.
        """
        return len(text) // 4
