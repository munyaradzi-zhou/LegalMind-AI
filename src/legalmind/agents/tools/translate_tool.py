"""Semantic Kernel plugin wrapping Azure Translator.

Provides Dutch-English (and other language) translation for legal documents
using the Azure Translator service. Used primarily by the Drafting agent
but available to all agents when cross-language support is needed.
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.services import CognitiveServicesClient

logger = structlog.get_logger(__name__)


class TranslateTool:
    """Semantic Kernel plugin for legal text translation.

    Wraps Azure Translator to provide Dutch <-> English translation
    and other language pairs. Preserves legal terminology and formatting
    where possible.
    """

    def __init__(self, cognitive_client: CognitiveServicesClient | None = None) -> None:
        """Initialize the translate tool.

        Args:
            cognitive_client: An existing CognitiveServicesClient. If not
                provided, a new one is created using default configuration.
        """
        self._client = cognitive_client or CognitiveServicesClient()

    @kernel_function(
        name="translate_text",
        description=(
            "Translate legal text between languages. Supports Dutch (nl), English (en), "
            "German (de), French (fr), and other languages. Preserves legal terminology "
            "and document formatting. Use this when you need to translate legal documents, "
            "correspondence, or text passages."
        ),
    )
    async def translate_text(
        self,
        text: Annotated[str, "The text to translate"],
        source_language: Annotated[
            str,
            "ISO 639-1 source language code, e.g. 'nl' for Dutch, 'en' for English",
        ] = "nl",
        target_language: Annotated[
            str,
            "ISO 639-1 target language code, e.g. 'en' for English, 'nl' for Dutch",
        ] = "en",
    ) -> Annotated[str, "JSON object with the translated text and metadata"]:
        """Translate text from one language to another.

        Args:
            text: The text to translate.
            source_language: Source language code.
            target_language: Target language code.

        Returns:
            JSON string containing the translation and metadata.
        """
        logger.info(
            "translate_tool_invoked",
            text_length=len(text),
            source=source_language,
            target=target_language,
        )

        if source_language == target_language:
            return json.dumps(
                {
                    "translated_text": text,
                    "source_language": source_language,
                    "target_language": target_language,
                    "note": "Source and target languages are the same; no translation performed.",
                },
                ensure_ascii=False,
            )

        try:
            translations = await self._client.translate(
                texts=[text],
                source_language=source_language,
                target_language=target_language,
            )

            translated_text = translations[0] if translations else ""

            result = {
                "translated_text": translated_text,
                "source_language": source_language,
                "target_language": target_language,
                "original_length": len(text),
                "translated_length": len(translated_text),
            }

            logger.info(
                "translate_tool_result",
                translated_length=len(translated_text),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("translate_tool_error")
            return json.dumps(
                {
                    "error": "Translation failed. Please try again or with a shorter text.",
                    "translated_text": "",
                }
            )

    @kernel_function(
        name="translate_dutch_to_english",
        description=(
            "Convenience function to translate Dutch legal text to English. "
            "Use this when a Dutch document or passage needs to be understood "
            "by an English-speaking audience."
        ),
    )
    async def translate_dutch_to_english(
        self,
        text: Annotated[str, "The Dutch text to translate to English"],
    ) -> Annotated[str, "JSON object with the English translation"]:
        """Translate Dutch text to English.

        Args:
            text: The Dutch text to translate.

        Returns:
            JSON string containing the English translation.
        """
        result: str = await self.translate_text(
            text=text,
            source_language="nl",
            target_language="en",
        )
        return result

    @kernel_function(
        name="translate_english_to_dutch",
        description=(
            "Convenience function to translate English text to Dutch. "
            "Use this when content needs to be converted to Dutch for "
            "Dutch legal documents or client communications."
        ),
    )
    async def translate_english_to_dutch(
        self,
        text: Annotated[str, "The English text to translate to Dutch"],
    ) -> Annotated[str, "JSON object with the Dutch translation"]:
        """Translate English text to Dutch.

        Args:
            text: The English text to translate.

        Returns:
            JSON string containing the Dutch translation.
        """
        result: str = await self.translate_text(
            text=text,
            source_language="en",
            target_language="nl",
        )
        return result
