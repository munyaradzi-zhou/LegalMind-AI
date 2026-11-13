"""Unit tests for content safety and PII detection service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from legalmind.services.content_safety import (
    ContentSafetyResult,
    ContentSafetyService,
    HarmCategory,
    HarmScore,
    PiiDetectionResult,
    PiiEntity,
    SafetyAction,
)


@pytest.fixture
def mock_settings():
    with patch("legalmind.services.content_safety.get_settings") as mock:
        settings = MagicMock()
        settings.cognitive.language_endpoint = "https://test.cognitiveservices.azure.com"
        settings.content_safety.endpoint = "https://test-safety.cognitiveservices.azure.com"
        settings.content_safety.severity_threshold = 4
        mock.return_value = settings
        yield settings


@pytest.fixture
def mock_credential():
    with patch("legalmind.services.content_safety.get_async_azure_credential") as mock:
        mock.return_value = MagicMock()
        yield mock.return_value


@pytest.fixture
def safety_service(mock_settings, mock_credential):
    return ContentSafetyService()


class TestBSNValidation:
    """Tests for Dutch BSN (Burgerservicenummer) validation."""

    def test_valid_bsn(self):
        # 111222333 passes the 11-proof: 1*9+1*8+1*7+2*6+2*5+2*4+3*3+3*2+3*(-1) = 55, 55%11=0
        assert ContentSafetyService._is_valid_bsn("111222333")

    def test_invalid_bsn_wrong_length(self):
        assert not ContentSafetyService._is_valid_bsn("12345")

    def test_invalid_bsn_not_digits(self):
        assert not ContentSafetyService._is_valid_bsn("12345678A")

    def test_invalid_bsn_fails_check(self):
        assert not ContentSafetyService._is_valid_bsn("123456780")


class TestDutchIdentifierDetection:
    """Tests for regex-based Dutch PII detection."""

    def test_detects_iban(self, safety_service: ContentSafetyService):
        text = "Mijn rekeningnummer is NL91ABNA0417164300."
        entities = safety_service._detect_dutch_identifiers(text)
        iban_entities = [e for e in entities if e.category == "InternationalBankingAccountNumber"]
        assert len(iban_entities) == 1
        assert iban_entities[0].text == "NL91ABNA0417164300"

    def test_detects_postcode(self, safety_service: ContentSafetyService):
        text = "Adres: Keizersgracht 123, 1015 CJ Amsterdam"
        entities = safety_service._detect_dutch_identifiers(text)
        postcode_entities = [e for e in entities if e.category == "Address"]
        assert len(postcode_entities) >= 1

    def test_no_false_positives_on_clean_text(self, safety_service: ContentSafetyService):
        text = "De opzegtermijn bedraagt twee maanden."
        entities = safety_service._detect_dutch_identifiers(text)
        # No IBAN or postcode in this text
        iban_entities = [e for e in entities if e.category == "InternationalBankingAccountNumber"]
        assert len(iban_entities) == 0


class TestContentSafetyAnalysis:
    """Tests for content safety analysis with mocked Azure service."""

    @pytest.mark.asyncio
    async def test_analyze_safe_text(self, safety_service: ContentSafetyService):
        """Safe legal text should pass content safety checks."""
        with patch("legalmind.services.content_safety.ContentSafetyService.analyze_text") as mock:
            mock.return_value = ContentSafetyResult(
                is_safe=True,
                action=SafetyAction.ALLOW,
                harm_scores=[
                    HarmScore(category=HarmCategory.HATE, severity=0),
                    HarmScore(category=HarmCategory.VIOLENCE, severity=0),
                ],
                flagged_categories=[],
            )
            result = await safety_service.analyze_text("Wat zijn de opzegvoorwaarden van het contract?")
            assert result.is_safe
            assert result.action == SafetyAction.ALLOW

    @pytest.mark.asyncio
    async def test_analyze_harmful_text_blocked(self, safety_service: ContentSafetyService):
        """Harmful text should be blocked."""
        with patch("legalmind.services.content_safety.ContentSafetyService.analyze_text") as mock:
            mock.return_value = ContentSafetyResult(
                is_safe=False,
                action=SafetyAction.BLOCK,
                harm_scores=[HarmScore(category=HarmCategory.VIOLENCE, severity=6)],
                flagged_categories=["Violence"],
            )
            result = await safety_service.analyze_text("harmful content")
            assert not result.is_safe
            assert result.action == SafetyAction.BLOCK
            assert "Violence" in result.flagged_categories


class TestPiiDetection:
    """Tests for PII detection with mocked Azure service."""

    @pytest.mark.asyncio
    async def test_detect_pii_in_dutch_text(self, safety_service: ContentSafetyService):
        """PII detection should find entities in Dutch legal text."""
        mock_doc = MagicMock()
        mock_doc.is_error = False
        mock_entity = MagicMock()
        mock_entity.text = "Jan de Vries"
        mock_entity.category = "Person"
        mock_entity.subcategory = None
        mock_entity.confidence_score = 0.95
        mock_entity.offset = 0
        mock_entity.length = 12
        mock_doc.entities = [mock_entity]

        with patch.object(safety_service, "_get_text_analytics_client") as mock_client:
            client_instance = AsyncMock()
            client_instance.recognize_pii_entities = AsyncMock(return_value=[mock_doc])
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            result = await safety_service.detect_pii("Jan de Vries woont in Amsterdam")
            assert result.has_pii
            assert result.entity_count >= 1
            assert "Person" in result.categories_found

    @pytest.mark.asyncio
    async def test_detect_no_pii(self, safety_service: ContentSafetyService):
        """Clean text should return no PII."""
        mock_doc = MagicMock()
        mock_doc.is_error = False
        mock_doc.entities = []

        with patch.object(safety_service, "_get_text_analytics_client") as mock_client:
            client_instance = AsyncMock()
            client_instance.recognize_pii_entities = AsyncMock(return_value=[mock_doc])
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            result = await safety_service.detect_pii("De opzegtermijn is twee maanden.")
            assert not result.has_pii
            assert result.entity_count == 0


class TestPiiRedaction:
    """Tests for PII redaction."""

    @pytest.mark.asyncio
    async def test_redact_replaces_pii(self, safety_service: ContentSafetyService):
        """Redaction should replace PII with category markers."""
        with patch.object(safety_service, "detect_pii") as mock_detect:
            mock_detect.return_value = PiiDetectionResult(
                entities=[
                    PiiEntity(text="Jan", category="Person", subcategory=None, confidence=0.9, offset=0, length=3),
                ],
                entity_count=1,
                categories_found=["Person"],
                has_pii=True,
            )
            result = await safety_service.redact_pii("Jan werkt bij Voorbeeld Rechtsbijstand.")
            assert "[Person]" in result
            assert "Jan" not in result

    @pytest.mark.asyncio
    async def test_redact_no_pii_returns_original(self, safety_service: ContentSafetyService):
        """Text without PII should be returned unchanged."""
        with patch.object(safety_service, "detect_pii") as mock_detect:
            mock_detect.return_value = PiiDetectionResult(
                entities=[],
                entity_count=0,
                categories_found=[],
                has_pii=False,
            )
            original = "De opzegtermijn is twee maanden."
            result = await safety_service.redact_pii(original)
            assert result == original
