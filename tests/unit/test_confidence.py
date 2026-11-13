"""Unit tests for the heuristic retrieval-confidence scorer."""

from __future__ import annotations

from dataclasses import dataclass, field

from legalmind.retrieval.confidence import (
    _LOW_CONFIDENCE_DISCLAIMER_NL,
    ConfidenceLevel,
    ConfidenceScore,
    RetrievalConfidenceScorer,
)

# ---------------------------------------------------------------------------
# Lightweight stub for SearchResult
# ---------------------------------------------------------------------------
# We avoid importing the real SearchResult to keep these tests free of
# Azure SDK dependencies.  The scorer only reads .content, .search_score,
# and .reranker_score — so a lightweight stand-in suffices.


@dataclass(slots=True)
class _FakeSearchResult:
    """Minimal stand-in for ``SearchResult``."""

    id: str = "chunk-0"
    document_id: str = "doc-0"
    chunk_index: int = 0
    content: str = ""
    title: str = ""
    document_type: str = "contract"
    language: str = "nl"
    page_number: int | None = None
    section_title: str | None = None
    entities: list[str] = field(default_factory=list)
    key_phrases: list[str] = field(default_factory=list)
    summary: str | None = None
    parent_id: str | None = None
    source_url: str | None = None
    created_at: str | None = None
    search_score: float = 0.0
    reranker_score: float | None = None


def _make_result(
    *,
    search_score: float = 0.0,
    reranker_score: float | None = None,
    content: str = "",
    result_id: str = "chunk-0",
) -> _FakeSearchResult:
    return _FakeSearchResult(
        id=result_id,
        content=content,
        search_score=search_score,
        reranker_score=reranker_score,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetrievalConfidenceScorer:
    """Tests for the heuristic confidence scorer."""

    def test_high_confidence_strong_top_score_and_gap(self):
        """A dominant top result with a large gap yields HIGH confidence."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(search_score=0.95, content="opzegtermijn twee maanden artikel", result_id="c1"),
            _make_result(search_score=0.55, content="verzekering premie", result_id="c2"),
            _make_result(search_score=0.50, content="dekking polis", result_id="c3"),
            _make_result(search_score=0.48, content="opzegging contractperiode", result_id="c4"),
            _make_result(search_score=0.45, content="opzegtermijn verlopen", result_id="c5"),
        ]

        cs = scorer.score("Wat is de opzegtermijn?", results)

        assert isinstance(cs, ConfidenceScore)
        assert cs.level == ConfidenceLevel.HIGH
        assert cs.score >= 0.80
        assert not cs.should_add_disclaimer
        assert cs.disclaimer == ""

    def test_medium_confidence_moderate_scores(self):
        """Moderate scores across the board yield MEDIUM confidence."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(search_score=0.60, content="opzegtermijn", result_id="c1"),
            _make_result(search_score=0.55, content="contract beëindiging", result_id="c2"),
            _make_result(search_score=0.50, content="polis", result_id="c3"),
        ]

        cs = scorer.score("Wat is de opzegtermijn van het contract?", results)

        assert cs.level == ConfidenceLevel.MEDIUM
        assert 0.50 <= cs.score < 0.80
        assert not cs.should_add_disclaimer

    def test_low_confidence_all_below_threshold(self):
        """When every result score is low, confidence should be LOW."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(search_score=0.20, content="onbelangrijke tekst", result_id="c1"),
            _make_result(search_score=0.15, content="andere tekst", result_id="c2"),
        ]

        cs = scorer.score("Wat zijn de gevolgen van ontslag op staande voet?", results)

        assert cs.level == ConfidenceLevel.LOW
        assert cs.score < 0.50
        assert cs.should_add_disclaimer
        assert cs.disclaimer == _LOW_CONFIDENCE_DISCLAIMER_NL

    def test_empty_results_returns_low_confidence(self):
        """An empty result set should yield LOW confidence with score 0.0."""
        scorer = RetrievalConfidenceScorer()
        cs = scorer.score("Wat is de opzegtermijn?", [])

        assert cs.level == ConfidenceLevel.LOW
        assert cs.score == 0.0
        assert cs.should_add_disclaimer
        assert "geen" in cs.reasoning.lower() or "no" in cs.reasoning.lower()

    def test_single_result_scoring(self):
        """A single high-scoring result should still yield reasonable confidence."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(
                search_score=0.90,
                content="opzegtermijn twee maanden artikel 12",
                result_id="c1",
            ),
        ]

        cs = scorer.score("Wat is de opzegtermijn?", results)

        # Single result with high score should give at least MEDIUM.
        assert cs.level in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)
        assert cs.score > 0.45

    def test_reranker_score_preferred_over_search_score(self):
        """When reranker_score is present it should be used for scoring."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(
                search_score=0.30,
                reranker_score=3.8,  # High on the 0-4 scale -> 0.95
                content="opzegtermijn verzekering twee maanden",
                result_id="c1",
            ),
            _make_result(
                search_score=0.25,
                reranker_score=1.0,  # 0.25 normalised
                content="andere tekst",
                result_id="c2",
            ),
            _make_result(search_score=0.50, content="opzegtermijn", result_id="c3"),
            _make_result(search_score=0.45, content="verzekering", result_id="c4"),
            _make_result(search_score=0.42, content="maanden twee", result_id="c5"),
        ]

        cs = scorer.score("Wat is de opzegtermijn van de verzekering?", results)

        assert cs.level == ConfidenceLevel.HIGH
        assert cs.components["top_score"] >= 0.90

    def test_query_coverage_affects_score(self):
        """Results that contain query terms should yield higher coverage."""
        scorer = RetrievalConfidenceScorer()

        # Good coverage: results contain the query terms.
        good_results = [
            _make_result(
                search_score=0.70,
                content="De aansprakelijkheid van de werkgever bij een arbeidsongeval",
                result_id="c1",
            ),
            _make_result(
                search_score=0.65,
                content="werkgever aansprakelijk arbeidsongeval zorgplicht",
                result_id="c2",
            ),
        ]

        # Poor coverage: results do not match query terms at all.
        poor_results = [
            _make_result(
                search_score=0.70,
                content="premie berekening jaarlijkse kosten",
                result_id="c1",
            ),
            _make_result(
                search_score=0.65,
                content="dekking water schade huis",
                result_id="c2",
            ),
        ]

        good_cs = scorer.score("Wie is aansprakelijk bij een arbeidsongeval?", good_results)
        poor_cs = scorer.score("Wie is aansprakelijk bij een arbeidsongeval?", poor_results)

        # Good coverage should produce a higher score.
        assert good_cs.score > poor_cs.score
        assert good_cs.components["query_coverage"] > poor_cs.components["query_coverage"]

    def test_components_present_in_result(self):
        """The components dict should contain all expected keys."""
        scorer = RetrievalConfidenceScorer()
        results = [_make_result(search_score=0.80, content="test", result_id="c1")]
        cs = scorer.score("test", results)

        expected_keys = {"top_score", "score_gap", "count_above_threshold", "query_coverage"}
        assert set(cs.components.keys()) == expected_keys

    def test_score_clamped_between_zero_and_one(self):
        """The final score must be in [0.0, 1.0] regardless of inputs."""
        scorer = RetrievalConfidenceScorer()
        results = [
            _make_result(search_score=5.0, content="test", result_id="c1"),
        ]
        cs = scorer.score("test", results)
        assert 0.0 <= cs.score <= 1.0

    def test_custom_thresholds(self):
        """Custom threshold parameters should be respected."""
        scorer = RetrievalConfidenceScorer(
            high_threshold=0.95,
            medium_threshold=0.70,
        )
        results = [
            _make_result(search_score=0.85, content="opzegtermijn", result_id="c1"),
            _make_result(search_score=0.50, content="contract", result_id="c2"),
        ]

        cs = scorer.score("opzegtermijn", results)
        # With a high_threshold of 0.95 and a top score of 0.85, it should
        # not be HIGH.
        assert cs.level in (ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW)
