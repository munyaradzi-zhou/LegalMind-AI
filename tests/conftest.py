"""Shared test fixtures for LegalMind AI tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def sample_document_bytes() -> bytes:
    """Sample PDF-like bytes for testing ingestion (not a real PDF)."""
    return b"%PDF-1.4 sample legal document content for testing"


@pytest.fixture
def sample_chunks() -> list[dict]:
    """Sample document chunks for testing retrieval and indexing."""
    return [
        {
            "id": "doc1-chunk-0",
            "document_id": "doc1",
            "chunk_index": 0,
            "content": (
                "Artikel 1: Definities. In deze overeenkomst wordt verstaan"
                " onder 'verzekerde': de natuurlijke persoon die als zodanig"
                " op de polis is vermeld."
            ),
            "title": "Verzekeringsovereenkomst 2024",
            "document_type": "contract",
            "language": "nl",
            "page_number": 1,
            "section_title": "Definities",
            "entities": ["verzekerde", "polis"],
            "key_phrases": ["definities", "overeenkomst", "natuurlijke persoon"],
            "summary": "Artikel 1 definieert de term 'verzekerde' als de persoon vermeld op de polis.",
        },
        {
            "id": "doc1-chunk-1",
            "document_id": "doc1",
            "chunk_index": 1,
            "content": (
                "Artikel 12: Opzegging. De verzekering kan door beide partijen worden"
                " opgezegd met inachtneming van een opzegtermijn van twee maanden"
                " voor het einde van de contractperiode."
            ),
            "title": "Verzekeringsovereenkomst 2024",
            "document_type": "contract",
            "language": "nl",
            "page_number": 5,
            "section_title": "Opzegging",
            "entities": ["verzekering", "contractperiode"],
            "key_phrases": ["opzegging", "opzegtermijn", "twee maanden"],
            "summary": "Artikel 12 beschrijft de opzegtermijn van twee maanden voor het einde van de contractperiode.",
        },
        {
            "id": "doc2-chunk-0",
            "document_id": "doc2",
            "chunk_index": 0,
            "content": (
                "Betreft: Aansprakelijkheidsstelling. Geachte heer/mevrouw,"
                " Hierbij stellen wij u aansprakelijk voor de schade die onze"
                " cliënt heeft geleden als gevolg van het arbeidsongeval op"
                " 15 maart 2024."
            ),
            "title": "Aansprakelijkheidsstelling arbeidsongeval",
            "document_type": "correspondence",
            "language": "nl",
            "page_number": 1,
            "section_title": "Aansprakelijkheidsstelling",
            "entities": ["15 maart 2024", "arbeidsongeval"],
            "key_phrases": ["aansprakelijkheid", "schade", "arbeidsongeval"],
            "summary": "Brief waarin de tegenpartij aansprakelijk wordt gesteld voor schade door een arbeidsongeval.",
        },
    ]


@pytest.fixture
def sample_search_results(sample_chunks: list[dict]) -> list[dict]:
    """Sample search results with scores."""
    results = []
    for i, chunk in enumerate(sample_chunks):
        result = dict(chunk)
        result["@search.score"] = 0.95 - (i * 0.1)
        result["@search.reranker_score"] = 3.5 - (i * 0.5)
        results.append(result)
    return results


@pytest.fixture
def sample_entities() -> list[list[dict]]:
    """Sample NER extraction results."""
    return [
        [
            {
                "text": "Voorbeeld Rechtsbijstand",
                "category": "Organization",
                "subcategory": None,
                "confidence": 0.95,
                "offset": 0,
                "length": 24,
            },
            {
                "text": "15 maart 2024",
                "category": "DateTime",
                "subcategory": "Date",
                "confidence": 0.99,
                "offset": 50,
                "length": 13,
            },
            {
                "text": "€5.000",
                "category": "Quantity",
                "subcategory": "Currency",
                "confidence": 0.92,
                "offset": 100,
                "length": 6,
            },
        ]
    ]


@pytest.fixture
def mock_openai_service():
    """Mock Azure OpenAI service for unit tests."""
    with patch("legalmind.services.azure_openai.AzureOpenAIService") as mock:
        service = MagicMock()
        service.chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": "Mocked response", "role": "assistant"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        )
        service.generate_embeddings = AsyncMock(return_value=[[0.1] * 3072])
        service.generate_single_embedding = AsyncMock(return_value=[0.1] * 3072)
        mock.return_value = service
        yield service


@pytest.fixture
def mock_search_service():
    """Mock Azure AI Search service for unit tests."""
    with patch("legalmind.services.search_client.SearchService") as mock:
        service = MagicMock()
        service.hybrid_search = AsyncMock(return_value=[])
        service.upload_documents = AsyncMock()
        mock.return_value = service
        yield service


@pytest.fixture
def mock_cognitive_services():
    """Mock Azure Cognitive Services for unit tests."""
    with patch("legalmind.services.cognitive_services.CognitiveServicesClient") as mock:
        service = MagicMock()
        service.extract_entities = AsyncMock(return_value=[[]])
        service.extract_key_phrases = AsyncMock(return_value=[[]])
        service.summarize_text = AsyncMock(return_value=[""])
        service.translate = AsyncMock(return_value=["Translated text"])
        service.analyze_document = AsyncMock(return_value={"content": "", "pages": [], "tables": [], "paragraphs": []})
        mock.return_value = service
        yield service
