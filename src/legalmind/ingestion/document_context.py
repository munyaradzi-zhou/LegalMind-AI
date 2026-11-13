"""Contextual chunk headers: tell every chunk which document it comes from.

A chunk cut out of its document loses its identity. "ARTIKEL 6 - PREMIE: De jaarpremie
bedraagt €180,00" doesn't say *which* insurance - so search, the answering agent and the
groundedness check can't tell a legal-expenses policy from a travel policy. At ingestion
one model call writes a one-sentence description of the whole document, which is put
above every chunk before it is embedded and indexed (also known as contextual retrieval).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from legalmind.ingestion.chunking import Chunk
    from legalmind.services.azure_openai import AzureOpenAIService

logger = structlog.get_logger(__name__)

HEADER_PREFIX = "Document: "
_MAX_INPUT_CHARS = 6000

_DESCRIBE_PROMPT = """\
Below is the start of a document titled "{title}".

Write ONE sentence (at most 35 words), in the language of the document, that identifies it:
what kind of document it is (for example the type of insurance policy, a letter, a case
summary), the main parties with their roles exactly as the document states them, and its
subject and reference number. Only state who wrote or issued the document if the document
says so explicitly; never infer it from a party that is merely mentioned.
This sentence will be placed above every passage of the document, so that a reader of a
single passage knows where it comes from. Do not summarise the content.

Document:
{text}
"""


async def describe_document(openai: AzureOpenAIService, text: str, title: str) -> str:
    """One-sentence identification of a document, or "" if it can't be generated."""
    if not text.strip():
        return ""
    try:
        result = await openai.chat_completion(
            messages=[{"role": "user", "content": _DESCRIBE_PROMPT.format(title=title, text=text[:_MAX_INPUT_CHARS])}],
            temperature=0.0,
            max_tokens=100,
        )
        description = str(result["choices"][0]["message"]["content"]).strip()
    except Exception:
        # Ingestion still works without the header; retrieval is just less precise.
        logger.warning("document_context_failed", title=title, exc_info=True)
        return ""
    description = " ".join(description.split())  # one line
    logger.info("document_context", title=title, description=description)
    return description


def add_context_header(chunks: list[Chunk], description: str) -> None:
    """Put "Document: <description>" above every chunk's content (in place)."""
    if not description:
        return
    header = f"{HEADER_PREFIX}{description}\n"
    for chunk in chunks:
        chunk.content = header + chunk.content
        chunk.char_count = len(chunk.content)


def strip_context_header(content: str) -> str:
    """The chunk's own text, without the document header (for showing excerpts)."""
    if content.startswith(HEADER_PREFIX):
        _, _, rest = content.partition("\n")
        return rest
    return content
