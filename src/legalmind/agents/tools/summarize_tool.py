"""Semantic Kernel plugin wrapping Azure AI Language summarization.

Provides extractive and abstractive text summarization for legal documents
using the Azure AI Language service. Used by the Research and Document
Analysis agents to condense large legal texts.
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.services import CognitiveServicesClient

logger = structlog.get_logger(__name__)

# Maximum input length for Azure AI Language summarization (in characters).
# Documents exceeding this are split into chunks before summarization.
_MAX_SUMMARIZATION_INPUT_LENGTH = 125_000


class SummarizeTool:
    """Semantic Kernel plugin for legal document summarization.

    Wraps Azure AI Language extractive summarization to produce concise
    summaries of Dutch legal texts while preserving key legal references,
    obligations, and dates.
    """

    def __init__(self, cognitive_client: CognitiveServicesClient | None = None) -> None:
        """Initialize the summarize tool.

        Args:
            cognitive_client: An existing CognitiveServicesClient. If not
                provided, a new one is created using default configuration.
        """
        self._client = cognitive_client or CognitiveServicesClient()

    @kernel_function(
        name="summarize_text",
        description=(
            "Summarize a legal document or text passage. Produces a concise extractive "
            "summary that preserves key legal terms, party names, dates, obligations, "
            "and article references. Best for long documents that need to be condensed."
        ),
    )
    async def summarize_text(
        self,
        text: Annotated[str, "The legal text to summarize"],
        max_sentences: Annotated[
            int,
            "Maximum number of sentences in the summary (default: 5, range: 1-20)",
        ] = 5,
        language: Annotated[
            str,
            "Language code of the input text: 'nl' for Dutch, 'en' for English (default: 'nl')",
        ] = "nl",
    ) -> Annotated[str, "JSON object with the summary text and metadata"]:
        """Summarize a legal text using Azure AI Language extractive summarization.

        For texts exceeding the service limit, the text is split into chunks,
        each chunk is summarized, and the summaries are concatenated.

        Args:
            text: The text to summarize.
            max_sentences: Maximum sentences in the summary.
            language: ISO 639-1 language code.

        Returns:
            JSON string containing the summary and metadata.
        """
        logger.info(
            "summarize_tool_invoked",
            text_length=len(text),
            max_sentences=max_sentences,
            language=language,
        )

        max_sentences = max(1, min(20, max_sentences))

        try:
            # Split into chunks if the text is too long
            chunks = self._split_text(text)

            summaries = await self._client.summarize_text(
                texts=chunks,
                language=language,
                max_sentence_count=max_sentences,
            )

            combined_summary = " ".join(s for s in summaries if s)

            result = {
                "summary": combined_summary,
                "original_length": len(text),
                "summary_length": len(combined_summary),
                "compression_ratio": (round(len(combined_summary) / len(text), 3) if text else 0.0),
                "chunks_processed": len(chunks),
                "language": language,
            }

            logger.info(
                "summarize_tool_result",
                summary_length=len(combined_summary),
                compression_ratio=result["compression_ratio"],
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("summarize_tool_error")
            return json.dumps(
                {
                    "error": "Summarization failed. The text may be too short or in an unsupported format.",
                    "summary": "",
                }
            )

    @kernel_function(
        name="summarize_multiple",
        description=(
            "Summarize multiple text passages at once. Useful for comparing or combining "
            "summaries of several related legal documents."
        ),
    )
    async def summarize_multiple(
        self,
        texts_json: Annotated[
            str,
            'JSON array of text strings to summarize, e.g. \'["text1", "text2"]\'',
        ],
        max_sentences: Annotated[int, "Maximum sentences per summary (default: 3)"] = 3,
        language: Annotated[str, "Language code (default: 'nl')"] = "nl",
    ) -> Annotated[str, "JSON array of summary objects"]:
        """Summarize multiple texts in a single call.

        Args:
            texts_json: JSON-encoded array of text strings.
            max_sentences: Maximum sentences per summary.
            language: ISO 639-1 language code.

        Returns:
            JSON string containing an array of summary objects.
        """
        logger.info("summarize_multiple_invoked", language=language)

        try:
            texts: list[str] = json.loads(texts_json)
        except (json.JSONDecodeError, TypeError):
            return json.dumps(
                {
                    "error": "Invalid JSON input. Provide a JSON array of strings.",
                }
            )

        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            return json.dumps(
                {
                    "error": "Input must be a JSON array of strings.",
                }
            )

        max_sentences = max(1, min(20, max_sentences))

        try:
            summaries = await self._client.summarize_text(
                texts=texts,
                language=language,
                max_sentence_count=max_sentences,
            )

            results = [
                {
                    "index": i,
                    "summary": summary,
                    "original_length": len(texts[i]),
                    "summary_length": len(summary),
                }
                for i, summary in enumerate(summaries)
            ]

            return json.dumps(results, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("summarize_multiple_error")
            return json.dumps(
                {
                    "error": "Batch summarization failed.",
                    "results": [],
                }
            )

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """Split text into chunks that fit within the summarization input limit.

        Splits on paragraph boundaries to preserve logical structure.

        Args:
            text: The text to split.

        Returns:
            A list of text chunks, each within the service limit.
        """
        if len(text) <= _MAX_SUMMARIZATION_INPUT_LENGTH:
            return [text]

        chunks: list[str] = []
        paragraphs = text.split("\n\n")
        current_chunk = ""

        for paragraph in paragraphs:
            if len(current_chunk) + len(paragraph) + 2 > _MAX_SUMMARIZATION_INPUT_LENGTH:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # If a single paragraph exceeds the limit, split it further
                if len(paragraph) > _MAX_SUMMARIZATION_INPUT_LENGTH:
                    sentences = paragraph.replace(". ", ".\n").split("\n")
                    for sentence in sentences:
                        if len(current_chunk) + len(sentence) + 1 > _MAX_SUMMARIZATION_INPUT_LENGTH:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = sentence
                        else:
                            current_chunk = f"{current_chunk} {sentence}" if current_chunk else sentence
                else:
                    current_chunk = paragraph
            else:
                current_chunk = f"{current_chunk}\n\n{paragraph}" if current_chunk else paragraph

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks
