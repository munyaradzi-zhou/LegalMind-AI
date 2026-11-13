"""Integration tests for Azure Cognitive Services.

Requires Azure credentials and live Cognitive Services resources.
Run with: pytest tests/integration/ -m integration
"""

from __future__ import annotations

import pytest

from legalmind.services.cognitive_services import CognitiveServicesClient


@pytest.mark.integration
class TestCognitiveServices:
    """End-to-end tests for Azure AI Language, Translator, and Document Intelligence."""

    @pytest.fixture
    def client(self) -> CognitiveServicesClient:
        return CognitiveServicesClient()

    @pytest.mark.asyncio
    async def test_entity_extraction_dutch(self, client: CognitiveServicesClient):
        """Test NER on Dutch legal text."""
        texts = ["Voorbeeld Rechtsbijstand heeft op 15 maart 2024 een schade van €5.000 gemeld bij de rechtbank."]
        results = await client.extract_entities(texts, language="nl")

        assert len(results) == 1
        entities = results[0]
        assert len(entities) > 0

        categories = [e["category"] for e in entities]
        assert "Organization" in categories or "DateTime" in categories

    @pytest.mark.asyncio
    async def test_key_phrase_extraction(self, client: CognitiveServicesClient):
        """Test key phrase extraction on Dutch text."""
        texts = ["De verzekerde is verplicht de schade binnen 30 dagen te melden bij de verzekeraar."]
        results = await client.extract_key_phrases(texts, language="nl")

        assert len(results) == 1
        assert len(results[0]) > 0

    @pytest.mark.asyncio
    async def test_translation_nl_to_en(self, client: CognitiveServicesClient):
        """Test Dutch to English translation."""
        texts = ["De opzegtermijn is twee maanden."]
        results = await client.translate(texts, source_language="nl", target_language="en")

        assert len(results) == 1
        assert "termination" in results[0].lower() or "notice" in results[0].lower() or "cancel" in results[0].lower()

    @pytest.mark.asyncio
    async def test_sentiment_analysis(self, client: CognitiveServicesClient):
        """Test sentiment analysis on Dutch text."""
        texts = ["Wij zijn zeer tevreden met de uitkomst van de zaak."]
        results = await client.analyze_sentiment(texts, language="nl")

        assert len(results) == 1
        assert results[0]["sentiment"] in ("positive", "neutral", "negative", "mixed")
