"""NER and metadata enrichment for document chunks.

Uses Azure AI Language (via :class:`CognitiveServicesClient`) to enrich each
chunk with named entities, key phrases, and optional per-chunk summaries.
Enriched metadata is stored alongside chunk content for filtered retrieval
and faceted search in Azure AI Search.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from legalmind.services import CognitiveServicesClient

if TYPE_CHECKING:
    from legalmind.ingestion.chunking import Chunk

logger = structlog.get_logger(__name__)

# Maximum number of text documents per Azure AI Language API call
_LANGUAGE_API_BATCH_SIZE = 25

# Maximum text length per document in a Language API call (5,120 characters)
_MAX_TEXT_LENGTH = 5120

# Entity categories relevant to Dutch legal documents
_LEGAL_ENTITY_CATEGORIES = frozenset(
    {
        "Person",
        "PersonType",
        "Organization",
        "DateTime",
        "Quantity",
        "Address",
        "PhoneNumber",
        "Email",
        "URL",
        "IP",
    }
)


@dataclass
class EnrichedChunk:
    """A chunk enriched with NER, key phrases, and optional summary.

    Attributes:
        chunk: The original :class:`Chunk` object.
        entities: Named entities extracted from the chunk text.
        key_phrases: Key phrases extracted from the chunk text.
        summary: Optional extractive summary of the chunk.
        persons: Deduplicated list of person names found in the chunk.
        organizations: Deduplicated list of organization names found.
        dates: Date references found in the chunk.
        monetary_amounts: Monetary values found in the chunk.
    """

    chunk: Chunk
    entities: list[dict[str, Any]] = field(default_factory=list)
    key_phrases: list[str] = field(default_factory=list)
    summary: str = ""
    persons: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    monetary_amounts: list[str] = field(default_factory=list)

    def to_metadata_dict(self) -> dict[str, Any]:
        """Flatten enrichment data into a metadata dictionary.

        Suitable for storing as search document fields in Azure AI Search.

        Returns:
            Dictionary with all enrichment metadata.
        """
        return {
            "entities": self.entities,
            "key_phrases": self.key_phrases,
            "summary": self.summary,
            "persons": self.persons,
            "organizations": self.organizations,
            "dates": self.dates,
            "monetary_amounts": self.monetary_amounts,
            **self.chunk.metadata,
        }


class ChunkEnricher:
    """Enriches document chunks with NER, key phrases, and summaries.

    Uses :class:`CognitiveServicesClient` to call Azure AI Language APIs.
    Processes chunks in configurable batches to stay within API limits.

    Usage::

        enricher = ChunkEnricher()
        enriched = await enricher.enrich_chunks(chunks, language="nl")
    """

    def __init__(
        self,
        cognitive_client: CognitiveServicesClient | None = None,
        *,
        batch_size: int = _LANGUAGE_API_BATCH_SIZE,
        include_summary: bool = True,
        language: str = "nl",
    ) -> None:
        """Initialize the chunk enricher.

        Args:
            cognitive_client: An existing ``CognitiveServicesClient`` instance.
                If ``None``, a new one is created.
            batch_size: Number of chunks to process per API call.
            include_summary: Whether to generate per-chunk summaries.
            language: Language code for NER and key phrase extraction.
        """
        self._client = cognitive_client or CognitiveServicesClient()
        self._batch_size = min(batch_size, _LANGUAGE_API_BATCH_SIZE)
        self._include_summary = include_summary
        self._language = language

    async def enrich_chunks(
        self,
        chunks: list[Chunk],
        *,
        language: str | None = None,
    ) -> list[EnrichedChunk]:
        """Enrich a list of chunks with NER, key phrases, and summaries.

        Processes chunks in batches, running entity extraction and key phrase
        extraction concurrently for each batch.

        Args:
            chunks: List of chunks to enrich.
            language: Override the default language for this call.

        Returns:
            List of :class:`EnrichedChunk` objects in the same order as input.
        """
        if not chunks:
            return []

        lang = language or self._language

        logger.info(
            "enrich_chunks_start",
            chunk_count=len(chunks),
            language=lang,
            include_summary=self._include_summary,
        )

        enriched_chunks: list[EnrichedChunk] = [EnrichedChunk(chunk=chunk) for chunk in chunks]

        # Process in batches
        for batch_start in range(0, len(chunks), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(chunks))
            batch_chunks = chunks[batch_start:batch_end]
            batch_enriched = enriched_chunks[batch_start:batch_end]

            texts = [self._truncate_text(chunk.content) for chunk in batch_chunks]

            # Run entity extraction and key phrase extraction concurrently
            tasks: list[asyncio.Task[Any]] = []
            async with asyncio.TaskGroup() as tg:
                entity_task = tg.create_task(
                    self._client.extract_entities(texts, language=lang),
                )
                keyphrase_task = tg.create_task(
                    self._client.extract_key_phrases(texts, language=lang),
                )
                tasks = [entity_task, keyphrase_task]

                if self._include_summary:
                    summary_task = tg.create_task(
                        self._safe_summarize(texts, lang),
                    )
                    tasks.append(summary_task)

            # Unpack results
            entity_results = entity_task.result()
            keyphrase_results = keyphrase_task.result()
            summary_results = summary_task.result() if self._include_summary else [""] * len(texts)

            # Apply enrichment to each chunk in the batch
            for i, enriched in enumerate(batch_enriched):
                entities = entity_results[i] if i < len(entity_results) else []
                key_phrases = keyphrase_results[i] if i < len(keyphrase_results) else []
                summary = summary_results[i] if i < len(summary_results) else ""

                enriched.entities = entities
                enriched.key_phrases = key_phrases
                enriched.summary = summary

                # Extract typed entity lists
                enriched.persons = self._extract_entities_by_category(entities, "Person")
                enriched.organizations = self._extract_entities_by_category(entities, "Organization")
                enriched.dates = self._extract_entities_by_category(entities, "DateTime")
                enriched.monetary_amounts = self._extract_entities_by_category(entities, "Quantity")

            logger.debug(
                "enrich_batch_complete",
                batch_start=batch_start,
                batch_size=len(batch_chunks),
            )

        logger.info("enrich_chunks_complete", chunk_count=len(enriched_chunks))
        return enriched_chunks

    async def enrich_single(self, chunk: Chunk, *, language: str | None = None) -> EnrichedChunk:
        """Enrich a single chunk.

        Convenience method that wraps :meth:`enrich_chunks` for a single chunk.

        Args:
            chunk: The chunk to enrich.
            language: Override the default language.

        Returns:
            An :class:`EnrichedChunk`.
        """
        results = await self.enrich_chunks([chunk], language=language)
        return results[0]

    async def _safe_summarize(self, texts: list[str], language: str) -> list[str]:
        """Generate summaries, returning empty strings on failure.

        Summarization may fail for very short texts, so we handle errors
        gracefully and return empty summaries instead of failing the batch.

        Args:
            texts: List of text strings.
            language: Language code.

        Returns:
            List of summary strings (empty on failure).
        """
        try:
            return await self._client.summarize_text(
                texts,
                language=language,
                max_sentence_count=3,
            )
        except Exception:
            logger.warning("summarization_failed", text_count=len(texts), exc_info=True)
            return [""] * len(texts)

    @staticmethod
    def _truncate_text(text: str) -> str:
        """Truncate text to the API maximum length.

        Tries to break at a sentence boundary when truncating.

        Args:
            text: Input text.

        Returns:
            Text truncated to at most ``_MAX_TEXT_LENGTH`` characters.
        """
        if len(text) <= _MAX_TEXT_LENGTH:
            return text

        truncated = text[:_MAX_TEXT_LENGTH]
        # Try to break at last sentence end
        last_period = truncated.rfind(". ")
        if last_period > _MAX_TEXT_LENGTH // 2:
            return truncated[: last_period + 1]
        return truncated

    @staticmethod
    def _extract_entities_by_category(
        entities: list[dict[str, Any]],
        category: str,
    ) -> list[str]:
        """Extract deduplicated entity texts for a given category.

        Args:
            entities: List of entity dicts (from Azure AI Language).
            category: The entity category to filter by.

        Returns:
            Deduplicated list of entity text strings.
        """
        seen: set[str] = set()
        result: list[str] = []

        for entity in entities:
            if entity.get("category") == category:
                text = entity.get("text", "").strip()
                normalized = text.lower()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    result.append(text)

        return result
