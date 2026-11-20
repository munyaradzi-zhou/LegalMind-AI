"""Azure Cognitive Services client — Language (NER, summarization) and Translator.

Document layout extraction lives in :mod:`legalmind.ingestion.document_intelligence`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from azure.ai.textanalytics.aio import TextAnalyticsClient
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger(__name__)

# Azure AI Language synchronous-API limits: at most 5 documents per request
# (entity recognition), and 5,120 characters per document.
LANGUAGE_BATCH_SIZE = 5
LANGUAGE_MAX_CHARS = 5120


class CognitiveServicesClient:
    """Unified client for Azure Cognitive Services.

    Wraps:
    - Azure AI Language (NER, key phrase extraction, summarization, sentiment)
    - Azure Translator (Dutch <-> English)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._language_endpoint = settings.cognitive.language_endpoint
        self._translator_endpoint = settings.cognitive.translator_endpoint
        self._translator_region = settings.cognitive.translator_region

    # ─── Azure AI Language ────────────────────────────────────────────────

    def _get_text_analytics_client(self) -> TextAnalyticsClient:
        return TextAnalyticsClient(
            endpoint=self._language_endpoint,
            credential=self._credential,
        )

    async def _analyze_in_batches(
        self,
        texts: list[str],
        analyze: Callable[[TextAnalyticsClient, list[str]], Awaitable[Any]],
    ) -> list[Any]:
        """Run a Language operation within the service limits; one result per input text.

        Texts are trimmed to the per-document limit and sent in batches of five.
        Empty texts are not sent; their result is ``None``.
        """
        results: list[Any] = [None] * len(texts)
        pending = [(i, text[:LANGUAGE_MAX_CHARS]) for i, text in enumerate(texts) if text.strip()]
        client = self._get_text_analytics_client()
        async with client:
            for start in range(0, len(pending), LANGUAGE_BATCH_SIZE):
                batch = pending[start : start + LANGUAGE_BATCH_SIZE]
                docs = await analyze(client, [text for _, text in batch])
                for (index, _), doc in zip(batch, docs, strict=True):
                    results[index] = doc
        return results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def extract_entities(self, texts: list[str], *, language: str = "nl") -> list[list[dict[str, Any]]]:
        """Extract named entities from text using Azure AI Language.

        Identifies persons, organizations, dates, monetary amounts, locations,
        and legal references in legal documents.

        Args:
            texts: List of text strings to analyze.
            language: Language code (default: Dutch 'nl').

        Returns:
            List of entity lists, one per input text.
        """
        logger.info("extract_entities", text_count=len(texts), language=language)

        response = await self._analyze_in_batches(
            texts, lambda client, batch: client.recognize_entities(batch, language=language)
        )

        results: list[Any] = []
        for doc in response:
            if doc is None:
                results.append([])
                continue
            if doc.is_error:
                logger.warning("entity_extraction_error", error=doc.error.message)
                results.append([])
                continue

            entities = [
                {
                    "text": entity.text,
                    "category": entity.category,
                    "subcategory": entity.subcategory,
                    "confidence": entity.confidence_score,
                    "offset": entity.offset,
                    "length": entity.length,
                }
                for entity in doc.entities
            ]
            results.append(entities)

        return results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def extract_key_phrases(self, texts: list[str], *, language: str = "nl") -> list[list[str]]:
        """Extract key phrases from text using Azure AI Language.

        Args:
            texts: List of text strings.
            language: Language code.

        Returns:
            List of key phrase lists, one per input text.
        """
        logger.info("extract_key_phrases", text_count=len(texts))

        response = await self._analyze_in_batches(
            texts, lambda client, batch: client.extract_key_phrases(batch, language=language)
        )

        results: list[Any] = []
        for doc in response:
            if doc is None:
                results.append([])
                continue
            if doc.is_error:
                logger.warning("key_phrase_error", error=doc.error.message)
                results.append([])
                continue
            results.append(list(doc.key_phrases))

        return results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def analyze_sentiment(self, texts: list[str], *, language: str = "nl") -> list[dict[str, Any]]:
        """Analyze sentiment of text using Azure AI Language.

        Args:
            texts: List of text strings.
            language: Language code.

        Returns:
            List of sentiment results with overall and per-sentence scores.
        """
        logger.info("analyze_sentiment", text_count=len(texts))

        response = await self._analyze_in_batches(
            texts, lambda client, batch: client.analyze_sentiment(batch, language=language)
        )

        results: list[Any] = []
        for doc in response:
            if doc is None:
                results.append({"sentiment": "unknown", "error": "empty text"})
                continue
            if doc.is_error:
                results.append({"sentiment": "unknown", "error": doc.error.message})
                continue
            results.append(
                {
                    "sentiment": doc.sentiment,
                    "confidence_scores": {
                        "positive": doc.confidence_scores.positive,
                        "neutral": doc.confidence_scores.neutral,
                        "negative": doc.confidence_scores.negative,
                    },
                }
            )

        return results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def summarize_text(
        self,
        texts: list[str],
        *,
        language: str = "nl",
        max_sentence_count: int = 5,
    ) -> list[str]:
        """Generate extractive summaries using Azure AI Language.

        Args:
            texts: List of text strings to summarize.
            language: Language code.
            max_sentence_count: Maximum sentences in the summary.

        Returns:
            List of summary strings.
        """
        logger.info("summarize_text", text_count=len(texts), max_sentences=max_sentence_count)

        client = self._get_text_analytics_client()
        async with client:
            poller = await client.begin_extract_summary(
                texts,
                language=language,
                max_sentence_count=max_sentence_count,
            )
            results = await poller.result()

        summaries = []
        async for doc in results:
            if doc.is_error:
                logger.warning("summarization_error", error=doc.error.message)
                summaries.append("")
                continue
            summary = " ".join(sentence.text for sentence in doc.sentences)
            summaries.append(summary)

        return summaries

    # ─── Azure Translator ────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def translate(
        self,
        texts: list[str],
        *,
        source_language: str = "nl",
        target_language: str = "en",
    ) -> list[str]:
        """Translate texts using Azure Translator.

        Args:
            texts: List of text strings to translate.
            source_language: Source language code.
            target_language: Target language code.

        Returns:
            List of translated strings.
        """
        import httpx

        logger.info("translate", text_count=len(texts), source=source_language, target=target_language)

        url = f"{self._translator_endpoint}/translate"
        params = {
            "api-version": "3.0",
            "from": source_language,
            "to": target_language,
        }
        body = [{"text": text} for text in texts]

        # Get token for Translator
        token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Region": self._translator_region,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, params=params, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        return [item["translations"][0]["text"] for item in data]
