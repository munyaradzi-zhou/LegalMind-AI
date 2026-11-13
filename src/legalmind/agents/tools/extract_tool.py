"""Semantic Kernel plugin wrapping Azure AI Language NER (Named Entity Recognition).

Provides entity extraction and key phrase extraction for legal documents using
Azure AI Language. Identifies persons, organizations, dates, monetary amounts,
legal references, and other structured information in Dutch legal texts.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.services import CognitiveServicesClient

logger = structlog.get_logger(__name__)


class ExtractTool:
    """Semantic Kernel plugin for legal entity and information extraction.

    Wraps Azure AI Language NER and key phrase extraction to identify
    structured information in Dutch legal documents — parties, dates,
    amounts, legal references, and key concepts.
    """

    def __init__(self, cognitive_client: CognitiveServicesClient | None = None) -> None:
        """Initialize the extract tool.

        Args:
            cognitive_client: An existing CognitiveServicesClient. If not
                provided, a new one is created using default configuration.
        """
        self._client = cognitive_client or CognitiveServicesClient()

    @kernel_function(
        name="extract_entities",
        description=(
            "Extract named entities from legal text. Identifies persons, organizations, "
            "dates, monetary amounts, locations, and legal references (article numbers, "
            "ECLI case numbers). Use this to parse structured information from unstructured "
            "legal documents."
        ),
    )
    async def extract_entities(
        self,
        text: Annotated[str, "The legal text to extract entities from"],
        language: Annotated[
            str,
            "Language code: 'nl' for Dutch (default), 'en' for English",
        ] = "nl",
    ) -> Annotated[str, "JSON object with categorized entities"]:
        """Extract named entities from legal text.

        Uses Azure AI Language NER to identify and categorize entities such as
        persons, organizations, dates, monetary amounts, and locations.

        Args:
            text: The text to analyze.
            language: ISO 639-1 language code.

        Returns:
            JSON string containing categorized entities.
        """
        logger.info(
            "extract_entities_invoked",
            text_length=len(text),
            language=language,
        )

        try:
            entity_results = await self._client.extract_entities(
                texts=[text],
                language=language,
            )

            entities = entity_results[0] if entity_results else []

            # Group entities by category for easier consumption
            categorized: dict[str, list[dict[str, Any]]] = {}
            for entity in entities:
                category = entity.get("category", "Unknown")
                if category not in categorized:
                    categorized[category] = []
                categorized[category].append(
                    {
                        "text": entity["text"],
                        "subcategory": entity.get("subcategory"),
                        "confidence": entity.get("confidence", 0.0),
                        "offset": entity.get("offset", 0),
                    }
                )

            # Deduplicate entities within each category (same text)
            for category in categorized:
                seen_texts: set[str] = set()
                unique_entities: list[dict[str, Any]] = []
                for entity in categorized[category]:
                    if entity["text"] not in seen_texts:
                        seen_texts.add(entity["text"])
                        unique_entities.append(entity)
                categorized[category] = unique_entities

            result = {
                "entities": categorized,
                "total_entities": sum(len(v) for v in categorized.values()),
                "categories_found": list(categorized.keys()),
                "language": language,
            }

            logger.info(
                "extract_entities_result",
                total_entities=result["total_entities"],
                categories=result["categories_found"],
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("extract_entities_error")
            return json.dumps(
                {
                    "error": "Entity extraction failed.",
                    "entities": {},
                }
            )

    @kernel_function(
        name="extract_key_phrases",
        description=(
            "Extract key phrases and important legal concepts from text. Returns the "
            "most significant terms and phrases that capture the main topics. Use this "
            "for understanding document themes or building keyword indexes."
        ),
    )
    async def extract_key_phrases(
        self,
        text: Annotated[str, "The legal text to extract key phrases from"],
        language: Annotated[str, "Language code (default: 'nl')"] = "nl",
    ) -> Annotated[str, "JSON object with key phrases"]:
        """Extract key phrases from legal text.

        Uses Azure AI Language key phrase extraction to identify the most
        important terms and concepts in the text.

        Args:
            text: The text to analyze.
            language: ISO 639-1 language code.

        Returns:
            JSON string containing key phrases.
        """
        logger.info(
            "extract_key_phrases_invoked",
            text_length=len(text),
            language=language,
        )

        try:
            phrase_results = await self._client.extract_key_phrases(
                texts=[text],
                language=language,
            )

            key_phrases = phrase_results[0] if phrase_results else []

            result = {
                "key_phrases": key_phrases,
                "count": len(key_phrases),
                "language": language,
            }

            logger.info("extract_key_phrases_result", count=len(key_phrases))
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("extract_key_phrases_error")
            return json.dumps(
                {
                    "error": "Key phrase extraction failed.",
                    "key_phrases": [],
                }
            )

    @kernel_function(
        name="extract_legal_references",
        description=(
            "Extract legal references from text, including article numbers "
            "(e.g., 'Art. 6:162 BW'), ECLI case numbers, law names, and "
            "regulation references. Specialized for Dutch legal documents."
        ),
    )
    async def extract_legal_references(
        self,
        text: Annotated[str, "The legal text to extract references from"],
    ) -> Annotated[str, "JSON object with categorized legal references"]:
        """Extract legal references from Dutch legal text.

        Combines NER with pattern matching to identify article references,
        ECLI numbers, and law name citations.

        Args:
            text: The text to analyze.

        Returns:
            JSON string containing legal references grouped by type.
        """
        import re

        logger.info("extract_legal_references_invoked", text_length=len(text))

        # Pattern-based extraction for legal references
        article_pattern = re.compile(
            r"(?:art(?:ikel)?\.?\s*)"
            r"(\d+[a-z]?(?::\d+[a-z]?)?(?:\s+(?:lid|sub)\s+\d+[a-z]?)*)"
            r"(?:\s+(?:van\s+(?:het|de)\s+)?(\w[\w\s]*?)(?=[\.,;)\]\s]|$))?",
            re.IGNORECASE,
        )
        ecli_pattern = re.compile(
            r"ECLI:NL:[A-Z]{2,8}:\d{4}:\d+",
            re.IGNORECASE,
        )
        law_name_pattern = re.compile(
            r"\b(?:Burgerlijk\s+Wetboek|BW|Wetboek\s+van\s+Strafrecht|Sr|WvSr|"
            r"Algemene\s+wet\s+bestuursrecht|Awb|AVG|GDPR|Wft|Wwft|"
            r"Faillissementswet|Fw|Wetboek\s+van\s+Burgerlijke\s+Rechtsvordering|Rv|"
            r"Handelsregisterwet|Hrw|Omgevingswet|Ow)\b",
            re.IGNORECASE,
        )

        articles = [
            {"article": m.group(1).strip(), "law": (m.group(2) or "").strip()} for m in article_pattern.finditer(text)
        ]
        ecli_numbers = list({m.group(0) for m in ecli_pattern.finditer(text)})
        law_names = list({m.group(0) for m in law_name_pattern.finditer(text)})

        # Also run NER to catch entities the patterns might miss
        try:
            entity_results = await self._client.extract_entities(
                texts=[text],
                language="nl",
            )
            ner_entities = entity_results[0] if entity_results else []
            # Filter for potentially legal entities (Quantity for article numbers, etc.)
            ner_legal = [
                {"text": e["text"], "category": e["category"]}
                for e in ner_entities
                if e.get("category") in ("DateTime", "Quantity", "Organization") and e.get("confidence", 0) > 0.7
            ]
        except Exception:
            logger.warning("extract_legal_references_ner_fallback")
            ner_legal = []

        result = {
            "article_references": articles,
            "ecli_numbers": ecli_numbers,
            "law_names": law_names,
            "ner_entities": ner_legal,
            "total_references": len(articles) + len(ecli_numbers) + len(law_names),
        }

        logger.info(
            "extract_legal_references_result",
            articles=len(articles),
            ecli=len(ecli_numbers),
            laws=len(law_names),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
