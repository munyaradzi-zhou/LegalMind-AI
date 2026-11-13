"""Heuristic retrieval-confidence scoring for search results.

Evaluates how confidently the retrieved documents answer the user's query
without making any LLM calls.  The score is based on observable properties
of the result set: top score, score distribution, result count, and query
term coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from legalmind.retrieval.hybrid_search import SearchResult

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_HIGH_THRESHOLD = 0.80
_DEFAULT_MEDIUM_THRESHOLD = 0.50
_DEFAULT_SCORE_GAP_WEIGHT = 0.15
_DEFAULT_COVERAGE_WEIGHT = 0.25
_DEFAULT_COUNT_WEIGHT = 0.15
_DEFAULT_TOP_SCORE_WEIGHT = 0.45

# Dutch stop-words to ignore when computing query coverage.
_DUTCH_STOP_WORDS: set[str] = {
    "de",
    "het",
    "een",
    "van",
    "in",
    "is",
    "op",
    "dat",
    "die",
    "en",
    "voor",
    "zijn",
    "met",
    "aan",
    "er",
    "als",
    "maar",
    "om",
    "dan",
    "ook",
    "te",
    "bij",
    "zo",
    "nog",
    "al",
    "was",
    "ik",
    "je",
    "ze",
    "we",
    "wat",
    "naar",
    "kan",
    "tot",
    "niet",
    "werd",
    "worden",
    "geen",
    "meer",
    "heeft",
    "deze",
    "uit",
    "door",
    "over",
    "hoe",
    "welke",
    "wordt",
    "mijn",
    "dit",
    "wel",
}

_LOW_CONFIDENCE_DISCLAIMER_NL = (
    "Let op: de gevonden bronnen zijn mogelijk niet volledig relevant voor uw "
    "vraag. Raadpleeg een juridisch specialist voor een betrouwbaar antwoord."
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ConfidenceLevel(StrEnum):
    """Qualitative confidence level."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(slots=True)
class ConfidenceScore:
    """Structured confidence assessment for a set of search results.

    Attributes:
        score: Numeric confidence between 0.0 and 1.0.
        level: Qualitative label (HIGH / MEDIUM / LOW).
        reasoning: Human-readable explanation of how the score was computed.
        should_add_disclaimer: Whether to prepend a disclaimer to the answer.
        disclaimer: The disclaimer text (Dutch), populated only when
            ``should_add_disclaimer`` is True.
        components: Breakdown of individual scoring components.
    """

    score: float
    level: ConfidenceLevel
    reasoning: str
    should_add_disclaimer: bool = False
    disclaimer: str = ""
    components: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class RetrievalConfidenceScorer:
    """Heuristic scorer that evaluates retrieval confidence without LLM calls.

    The final score is a weighted combination of four signals:

    1. **Top score** — the relevance score of the best result.
    2. **Score gap** — the difference between the #1 and #2 results.  A
       large gap means the top result is a clear winner, boosting confidence.
    3. **Count above threshold** — how many results exceed a minimum
       relevance threshold.
    4. **Query coverage** — the fraction of meaningful query terms that
       appear in the top results.

    Args:
        high_threshold: Minimum combined score for HIGH confidence.
        medium_threshold: Minimum combined score for MEDIUM confidence.
        top_score_weight: Weight for the top-result score signal.
        score_gap_weight: Weight for the score-gap signal.
        count_weight: Weight for the count-above-threshold signal.
        coverage_weight: Weight for the query-coverage signal.
        relevance_floor: Minimum per-result score to be counted as "above
            threshold" for the count signal.
    """

    def __init__(
        self,
        *,
        high_threshold: float = _DEFAULT_HIGH_THRESHOLD,
        medium_threshold: float = _DEFAULT_MEDIUM_THRESHOLD,
        top_score_weight: float = _DEFAULT_TOP_SCORE_WEIGHT,
        score_gap_weight: float = _DEFAULT_SCORE_GAP_WEIGHT,
        count_weight: float = _DEFAULT_COUNT_WEIGHT,
        coverage_weight: float = _DEFAULT_COVERAGE_WEIGHT,
        relevance_floor: float = 0.40,
    ) -> None:
        self._high_threshold = high_threshold
        self._medium_threshold = medium_threshold
        self._top_score_weight = top_score_weight
        self._score_gap_weight = score_gap_weight
        self._count_weight = count_weight
        self._coverage_weight = coverage_weight
        self._relevance_floor = relevance_floor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        query: str,
        results: list[SearchResult],
    ) -> ConfidenceScore:
        """Compute a confidence score for the given query and result set.

        Args:
            query: The user's search query.
            results: Ordered list of search results (best first).

        Returns:
            A :class:`ConfidenceScore` with numeric score, qualitative level,
            reasoning, and disclaimer flag.
        """
        logger.debug(
            "confidence_score_start",
            query=query[:120],
            result_count=len(results),
        )

        if not results:
            return self._empty_result()

        # --- Signal 1: top result score (normalised to 0-1) ---
        top_score_raw = self._effective_score(results[0])
        top_score_signal = min(top_score_raw, 1.0)

        # --- Signal 2: score gap between #1 and #2 ---
        if len(results) >= 2:
            second_score = self._effective_score(results[1])
            gap = top_score_raw - second_score
            # Normalise: a gap >= 0.3 maps to signal = 1.0
            score_gap_signal = min(gap / 0.3, 1.0) if gap > 0 else 0.0
        else:
            # Single result — neutral gap signal
            score_gap_signal = 0.5

        # --- Signal 3: count of results above relevance floor ---
        above_count = sum(1 for r in results if self._effective_score(r) >= self._relevance_floor)
        # Normalise: 5+ relevant results maps to 1.0
        count_signal = min(above_count / 5.0, 1.0)

        # --- Signal 4: query-term coverage in top results ---
        coverage_signal = self._compute_query_coverage(query, results[:5])

        # --- Weighted combination ---
        combined = (
            self._top_score_weight * top_score_signal
            + self._score_gap_weight * score_gap_signal
            + self._count_weight * count_signal
            + self._coverage_weight * coverage_signal
        )
        combined = round(max(0.0, min(1.0, combined)), 4)

        # --- Determine level ---
        if combined >= self._high_threshold:
            level = ConfidenceLevel.HIGH
        elif combined >= self._medium_threshold:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        should_disclaim = level == ConfidenceLevel.LOW

        # --- Build reasoning ---
        reasoning_parts: list[str] = [
            f"Top result score: {top_score_raw:.3f}",
            f"Score gap (1st-2nd): {score_gap_signal:.3f} (weighted)",
            f"Results above threshold ({self._relevance_floor}): {above_count}",
            f"Query term coverage: {coverage_signal:.2%}",
            f"Combined score: {combined:.4f} -> {level.value}",
        ]
        reasoning = "; ".join(reasoning_parts)

        components = {
            "top_score": round(top_score_signal, 4),
            "score_gap": round(score_gap_signal, 4),
            "count_above_threshold": round(count_signal, 4),
            "query_coverage": round(coverage_signal, 4),
        }

        result = ConfidenceScore(
            score=combined,
            level=level,
            reasoning=reasoning,
            should_add_disclaimer=should_disclaim,
            disclaimer=_LOW_CONFIDENCE_DISCLAIMER_NL if should_disclaim else "",
            components=components,
        )

        logger.info(
            "confidence_score_complete",
            score=result.score,
            level=result.level,
            should_disclaim=result.should_add_disclaimer,
        )

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_score(result: SearchResult) -> float:
        """Return the best available relevance score for a result.

        Prefers the semantic reranker score (typically 0-4 scale, normalised
        to 0-1) over the raw search score.
        """
        if result.reranker_score is not None:
            # Azure semantic reranker scores are on a 0-4 scale.
            return min(result.reranker_score / 4.0, 1.0)
        return min(result.search_score, 1.0)

    @staticmethod
    def _tokenise_query(query: str) -> list[str]:
        """Split query into lower-cased meaningful tokens."""
        tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", query.lower())
        return [t for t in tokens if t not in _DUTCH_STOP_WORDS and len(t) > 1]

    def _compute_query_coverage(
        self,
        query: str,
        results: list[SearchResult],
    ) -> float:
        """Fraction of query terms found in the top results' content."""
        terms = self._tokenise_query(query)
        if not terms:
            return 1.0  # Vacuously satisfied

        combined_text = " ".join(r.content.lower() for r in results)

        found = sum(1 for t in terms if t in combined_text)
        return found / len(terms)

    def _empty_result(self) -> ConfidenceScore:
        """Return a LOW-confidence score for an empty result set."""
        return ConfidenceScore(
            score=0.0,
            level=ConfidenceLevel.LOW,
            reasoning="No search results returned.",
            should_add_disclaimer=True,
            disclaimer=_LOW_CONFIDENCE_DISCLAIMER_NL,
            components={
                "top_score": 0.0,
                "score_gap": 0.0,
                "count_above_threshold": 0.0,
                "query_coverage": 0.0,
            },
        )
