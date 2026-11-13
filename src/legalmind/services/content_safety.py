"""Azure AI Content Safety and PII detection/redaction service.

Provides content moderation (hate, violence, self-harm, sexual) via Azure AI
Content Safety, and PII detection/redaction via Azure AI Language — tailored
for Dutch legal documents containing sensitive personal information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

import structlog
from azure.ai.textanalytics.aio import TextAnalyticsClient
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_settings

logger = structlog.get_logger(__name__)


# ─── Data models ─────────────────────────────────────────────────────────────


class HarmCategory(StrEnum):
    """Content Safety harm categories."""

    HATE = "Hate"
    VIOLENCE = "Violence"
    SELF_HARM = "SelfHarm"
    SEXUAL = "Sexual"


class SafetyAction(StrEnum):
    """Actions to take based on safety analysis."""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class HarmScore:
    """Score for a single harm category (0-6 scale)."""

    category: HarmCategory
    severity: int  # 0 = safe, 2 = low, 4 = medium, 6 = high


@dataclass(frozen=True, slots=True)
class ContentSafetyResult:
    """Result of a content safety analysis."""

    is_safe: bool
    action: SafetyAction
    harm_scores: list[HarmScore]
    flagged_categories: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PiiEntity:
    """A detected PII entity."""

    text: str
    category: str
    subcategory: str | None
    confidence: float
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class PiiDetectionResult:
    """Result of PII detection."""

    entities: list[PiiEntity]
    entity_count: int
    categories_found: list[str]
    has_pii: bool


# ─── Content Safety Service ──────────────────────────────────────────────────


class ContentSafetyService:
    """Azure AI Content Safety and PII detection for legal documents.

    Combines:
    - Azure AI Content Safety for harmful content detection
    - Azure AI Language PII recognition for Dutch personal data
    - Regex-based detection for Dutch-specific identifiers (BSN, IBAN)

    All methods use managed identity authentication and retry on transient failures.
    """

    # Dutch-specific PII patterns
    _BSN_PATTERN = re.compile(r"\b\d{9}\b")  # Burgerservicenummer (9 digits)
    _IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{10}\b")  # NL IBAN
    _POSTCODE_PATTERN = re.compile(r"\b\d{4}\s?[A-Z]{2}\b")  # Dutch postcode

    # PII categories relevant for Dutch legal context
    DEFAULT_PII_CATEGORIES: ClassVar[list[str]] = [
        "Person",
        "PersonType",
        "PhoneNumber",
        "Email",
        "Address",
        "DateTime",
        "Organization",
        "NLBSNNumber",
        "InternationalBankingAccountNumber",
    ]

    def __init__(
        self,
        *,
        severity_threshold: int | None = None,
        pii_categories: list[str] | None = None,
    ) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._language_endpoint = settings.cognitive.language_endpoint
        self._content_safety_endpoint = settings.content_safety.endpoint

        threshold = severity_threshold if severity_threshold is not None else settings.content_safety.severity_threshold
        self._severity_threshold = threshold
        self._pii_categories = pii_categories or self.DEFAULT_PII_CATEGORIES

    # ─── Text Analytics client ───────────────────────────────────────────

    def _get_text_analytics_client(self) -> TextAnalyticsClient:
        return TextAnalyticsClient(
            endpoint=self._language_endpoint,
            credential=self._credential,
        )

    # ─── Content Safety Analysis ─────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def analyze_text(self, text: str) -> ContentSafetyResult:
        """Analyze text for harmful content across all harm categories.

        Uses Azure AI Content Safety to detect hate speech, violence,
        self-harm, and sexual content. Returns severity scores (0-6)
        for each category.

        Args:
            text: The text to analyze.

        Returns:
            ContentSafetyResult with per-category scores and overall safety verdict.
        """
        logger.info("content_safety_analyze", text_length=len(text))

        if not self._content_safety_endpoint:
            # Local development without Azure AI Content Safety (same fallback as a missing SDK).
            logger.warning("content_safety_skipped", reason="AZURE_CONTENT_SAFETY_ENDPOINT not set")
            return ContentSafetyResult(
                is_safe=True,
                action=SafetyAction.ALLOW,
                harm_scores=[],
                flagged_categories=[],
            )

        try:
            from azure.ai.contentsafety.aio import ContentSafetyClient
            from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory

            async with ContentSafetyClient(
                endpoint=self._content_safety_endpoint,
                credential=self._credential,
            ) as client:
                response = await client.analyze_text(
                    AnalyzeTextOptions(text=text[:10000]),  # API limit
                )

            harm_scores = []
            flagged: list[str] = []

            category_map: dict[str, HarmCategory] = {
                TextCategory.HATE: HarmCategory.HATE,
                TextCategory.VIOLENCE: HarmCategory.VIOLENCE,
                TextCategory.SELF_HARM: HarmCategory.SELF_HARM,
                TextCategory.SEXUAL: HarmCategory.SEXUAL,
            }

            for result_item in response.categories_analysis:
                category = category_map.get(result_item.category, HarmCategory.HATE)
                severity = result_item.severity or 0
                harm_scores.append(HarmScore(category=category, severity=severity))

                if severity >= self._severity_threshold:
                    flagged.append(category.value)

            is_safe = len(flagged) == 0
            action = (
                SafetyAction.ALLOW
                if is_safe
                else (SafetyAction.BLOCK if any(s.severity >= 6 for s in harm_scores) else SafetyAction.WARN)
            )

            logger.info("content_safety_result", is_safe=is_safe, flagged=flagged)
            return ContentSafetyResult(
                is_safe=is_safe,
                action=action,
                harm_scores=harm_scores,
                flagged_categories=flagged,
            )

        except ImportError:
            logger.warning("content_safety_sdk_not_installed", fallback="allow")
            return ContentSafetyResult(
                is_safe=True,
                action=SafetyAction.ALLOW,
                harm_scores=[],
                flagged_categories=[],
            )
        except Exception:
            logger.exception("content_safety_error")
            # Fail open with a warning — don't block legitimate requests
            return ContentSafetyResult(
                is_safe=True,
                action=SafetyAction.WARN,
                harm_scores=[],
                flagged_categories=["analysis_error"],
            )

    # ─── PII Detection ───────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def detect_pii(self, text: str, *, language: str = "nl") -> PiiDetectionResult:
        """Detect personally identifiable information in text.

        Combines Azure AI Language PII recognition with regex-based
        detection for Dutch-specific identifiers (BSN, IBAN, postcodes).

        Args:
            text: The text to scan for PII.
            language: Language code (default: Dutch).

        Returns:
            PiiDetectionResult with all detected PII entities.
        """
        logger.info("pii_detect", text_length=len(text), language=language)

        entities: list[PiiEntity] = []

        # Azure AI Language PII recognition (skipped when no Language endpoint is configured)
        if not self._language_endpoint:
            logger.warning("pii_azure_detection_skipped", reason="AZURE_LANGUAGE_ENDPOINT not set")
        else:
            try:
                client = self._get_text_analytics_client()
                async with client:
                    response = await client.recognize_pii_entities(
                        [text[:5120]],  # Language per-document limit
                        language=language,
                        categories_filter=self._pii_categories,
                    )

                for doc in response:
                    if doc.is_error:
                        logger.warning("pii_detection_error", error=doc.error.message)
                        continue
                    for entity in doc.entities:
                        entities.append(
                            PiiEntity(
                                text=entity.text,
                                category=entity.category,
                                subcategory=entity.subcategory or "",
                                confidence=entity.confidence_score,
                                offset=entity.offset,
                                length=entity.length,
                            )
                        )
            except Exception:
                logger.exception("pii_azure_detection_error")

        # Supplement with regex-based Dutch identifiers
        entities.extend(self._detect_dutch_identifiers(text))

        categories_found = sorted({e.category for e in entities})

        result = PiiDetectionResult(
            entities=entities,
            entity_count=len(entities),
            categories_found=categories_found,
            has_pii=len(entities) > 0,
        )
        logger.info("pii_detect_result", entity_count=result.entity_count, categories=categories_found)
        return result

    def _detect_dutch_identifiers(self, text: str) -> list[PiiEntity]:
        """Detect Dutch-specific PII patterns via regex."""
        entities: list[PiiEntity] = []

        for match in self._BSN_PATTERN.finditer(text):
            if self._is_valid_bsn(match.group()):
                entities.append(
                    PiiEntity(
                        text=match.group(),
                        category="NLBSNNumber",
                        subcategory=None,
                        confidence=0.85,
                        offset=match.start(),
                        length=len(match.group()),
                    )
                )

        for match in self._IBAN_PATTERN.finditer(text):
            entities.append(
                PiiEntity(
                    text=match.group(),
                    category="InternationalBankingAccountNumber",
                    subcategory=None,
                    confidence=0.95,
                    offset=match.start(),
                    length=len(match.group()),
                )
            )

        for match in self._POSTCODE_PATTERN.finditer(text):
            entities.append(
                PiiEntity(
                    text=match.group(),
                    category="Address",
                    subcategory="Postcode",
                    confidence=0.80,
                    offset=match.start(),
                    length=len(match.group()),
                )
            )

        return entities

    @staticmethod
    def _is_valid_bsn(number: str) -> bool:
        """Validate a Dutch BSN using the 11-proof check."""
        if len(number) != 9 or not number.isdigit():
            return False
        digits = [int(d) for d in number]
        weights = [9, 8, 7, 6, 5, 4, 3, 2, -1]
        total = sum(d * w for d, w in zip(digits, weights, strict=True))
        return total % 11 == 0 and total != 0

    # ─── PII Redaction ───────────────────────────────────────────────────

    async def redact_pii(
        self,
        text: str,
        *,
        language: str = "nl",
        replacement_format: str = "[{category}]",
    ) -> str:
        """Detect and redact PII from text.

        Replaces detected PII entities with category markers
        (e.g., ``Jan de Vries`` → ``[Person]``).

        Args:
            text: The text to redact.
            language: Language code.
            replacement_format: Format string for replacements.

        Returns:
            The redacted text.
        """
        detection = await self.detect_pii(text, language=language)

        if not detection.has_pii:
            return text

        # Sort entities by offset in reverse order to preserve positions
        sorted_entities = sorted(detection.entities, key=lambda e: e.offset, reverse=True)
        redacted = text

        for entity in sorted_entities:
            replacement = replacement_format.format(category=entity.category)
            redacted = redacted[: entity.offset] + replacement + redacted[entity.offset + entity.length :]

        logger.info(
            "pii_redacted",
            original_length=len(text),
            redacted_length=len(redacted),
            entities_redacted=len(sorted_entities),
        )
        return redacted


def _build_analysis_summary(result: ContentSafetyResult) -> dict[str, Any]:
    """Build a summary dict for audit logging."""
    return {
        "is_safe": result.is_safe,
        "action": result.action.value,
        "flagged_categories": result.flagged_categories,
        "scores": {s.category.value: s.severity for s in result.harm_scores},
    }
