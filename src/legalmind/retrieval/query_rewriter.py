"""LLM-based query rewriting for improved legal document retrieval.

Provides three strategies:

1. **Rewrite** — rephrase a user query for better keyword / semantic recall.
2. **Decompose** — split a complex legal question into independent sub-queries.
3. **HyDE** — Hypothetical Document Embedding: generate a plausible answer
   passage and use its embedding for vector search.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from legalmind.services import AzureOpenAIService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured prompt templates
# ---------------------------------------------------------------------------

_REWRITE_SYSTEM_PROMPT = """\
You are a Dutch legal information retrieval specialist.  Your task is to
rewrite the user's search query so that it will perform better when used to
search an index of Dutch legal documents (contracts, correspondence, case
files, and policy documents).

Guidelines:
- Expand abbreviations (e.g., "BW" -> "Burgerlijk Wetboek").
- Add relevant synonyms and related legal terms.
- Preserve the original intent — do NOT change the meaning.
- If the query is in English, translate the rewritten query to Dutch as well
  and include both languages separated by a newline.
- Keep the rewritten query concise (max 3 sentences).
- Return ONLY the rewritten query text, with no additional commentary.
"""

_DECOMPOSE_SYSTEM_PROMPT = """\
You are a Dutch legal information retrieval specialist.  The user has a
complex legal question that may require information from multiple documents
or sections.

Your task is to break the question into 2-5 independent, self-contained
sub-queries.  Each sub-query should target a single, specific aspect of the
original question and be suitable for a keyword + vector search over Dutch
legal documents.

Respond with a JSON array of strings.  Example:
["sub-query 1", "sub-query 2", "sub-query 3"]

Return ONLY the JSON array, with no additional commentary.
"""

_HYDE_SYSTEM_PROMPT = """\
You are a Dutch legal expert.  Given the user's question, write a short
passage (150-250 words) that would be a plausible, authoritative answer
found in a Dutch legal document.

Guidelines:
- Write as if the passage were extracted from an actual contract, court
  ruling, or legal policy document.
- Use formal Dutch legal language.
- Include specific legal terms, article references, and procedural language
  where appropriate.
- The passage does NOT need to be factually correct — it only needs to
  *sound like* a real legal passage that answers the question, so that its
  embedding is close to relevant real documents.
- Return ONLY the passage text, with no additional commentary.
"""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    """The result of a query rewrite operation.

    Attributes:
        original: The user's original query.
        rewritten: The rewritten query for search.
    """

    original: str
    rewritten: str


@dataclass(frozen=True, slots=True)
class DecomposedQuery:
    """The result of a query decomposition operation.

    Attributes:
        original: The user's original query.
        sub_queries: Independent sub-queries derived from the original.
    """

    original: str
    sub_queries: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HydeResult:
    """The result of Hypothetical Document Embedding generation.

    Attributes:
        original_query: The user's original query.
        hypothetical_passage: The generated passage.
        embedding: The embedding vector of the hypothetical passage.
    """

    original_query: str
    hypothetical_passage: str
    embedding: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Query rewriter
# ---------------------------------------------------------------------------


class QueryRewriter:
    """LLM-powered query transformation for legal document retrieval.

    Uses GPT-4o via :class:`AzureOpenAIService` to improve search quality
    through query rewriting, decomposition, and Hypothetical Document
    Embedding (HyDE).
    """

    def __init__(self, *, openai_service: AzureOpenAIService | None = None) -> None:
        self._openai = openai_service or AzureOpenAIService()

    # ------------------------------------------------------------------
    # Rewrite
    # ------------------------------------------------------------------

    async def rewrite_query(self, query: str) -> RewrittenQuery:
        """Rewrite a user query for improved retrieval.

        Expands abbreviations, adds synonyms, and translates to Dutch where
        necessary.

        Args:
            query: The raw user query.

        Returns:
            A ``RewrittenQuery`` containing both original and rewritten forms.
        """
        logger.info("rewrite_query_start", query=query[:120])

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        response = await self._openai.chat_completion(
            messages,
            temperature=0.2,
            max_tokens=512,
        )

        rewritten = response["choices"][0]["message"]["content"].strip()

        logger.info(
            "rewrite_query_complete",
            original=query[:80],
            rewritten=rewritten[:80],
        )

        return RewrittenQuery(original=query, rewritten=rewritten)

    # ------------------------------------------------------------------
    # Decompose
    # ------------------------------------------------------------------

    async def decompose_query(self, query: str) -> DecomposedQuery:
        """Break a complex legal question into independent sub-queries.

        Each sub-query targets a specific facet of the original question and
        can be executed independently against the search index.

        Args:
            query: The complex user query.

        Returns:
            A ``DecomposedQuery`` with the original query and a list of
            sub-queries.

        Raises:
            ValueError: If the LLM response cannot be parsed as a JSON array.
        """
        logger.info("decompose_query_start", query=query[:120])

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _DECOMPOSE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        response = await self._openai.chat_completion(
            messages,
            temperature=0.3,
            max_tokens=1024,
        )

        raw_content = response["choices"][0]["message"]["content"].strip()

        try:
            sub_queries = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error(
                "decompose_query_parse_error",
                raw=raw_content[:200],
                error=str(exc),
            )
            raise ValueError(f"Failed to parse sub-queries from LLM response: {raw_content[:200]}") from exc

        if not isinstance(sub_queries, list) or not all(isinstance(q, str) for q in sub_queries):
            raise ValueError(f"Expected a JSON array of strings, got: {type(sub_queries).__name__}")

        logger.info(
            "decompose_query_complete",
            original=query[:80],
            sub_query_count=len(sub_queries),
        )

        return DecomposedQuery(original=query, sub_queries=sub_queries)

    # ------------------------------------------------------------------
    # HyDE — Hypothetical Document Embedding
    # ------------------------------------------------------------------

    async def generate_hyde(self, query: str) -> HydeResult:
        """Generate a Hypothetical Document Embedding for the query.

        Creates a synthetic passage that *sounds like* a real answer from a
        Dutch legal document, then embeds that passage.  The resulting
        embedding is closer in vector space to relevant real documents than
        the short user query alone would be.

        Args:
            query: The user's natural-language question.

        Returns:
            A ``HydeResult`` containing the hypothetical passage and its
            embedding vector.
        """
        logger.info("hyde_start", query=query[:120])

        # Step 1: Generate a hypothetical answer passage
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _HYDE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        response = await self._openai.chat_completion(
            messages,
            temperature=0.7,
            max_tokens=1024,
        )

        hypothetical_passage = response["choices"][0]["message"]["content"].strip()

        logger.info(
            "hyde_passage_generated",
            passage_length=len(hypothetical_passage),
        )

        # Step 2: Embed the hypothetical passage
        embedding = await self._openai.generate_single_embedding(hypothetical_passage)

        logger.info("hyde_complete", embedding_dims=len(embedding))

        return HydeResult(
            original_query=query,
            hypothetical_passage=hypothetical_passage,
            embedding=embedding,
        )
