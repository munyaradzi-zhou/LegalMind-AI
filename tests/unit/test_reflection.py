"""Unit tests for the self-reflection / correction loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from legalmind.agents.reflection import (
    IssueType,
    ReflectionEngine,
    ReflectionResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_openai():
    """Provide a mocked AzureOpenAIService for the reflection engine."""
    with patch("legalmind.agents.reflection.AzureOpenAIService") as mock_cls:
        service = AsyncMock()
        mock_cls.return_value = service
        yield service


def _make_evaluate_response(*, issues_found: bool, issues: list[dict] | None = None):
    """Helper: build a mock chat-completion return value for evaluation."""
    payload = {
        "issues_found": issues_found,
        "issues": issues or [],
    }
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
    }


def _make_correction_response(corrected_text: str):
    """Helper: build a mock chat-completion return value for correction."""
    return {
        "choices": [{"message": {"content": corrected_text}}],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReflectionEngine:
    """Tests for ReflectionEngine.reflect_and_correct."""

    @pytest.mark.asyncio
    async def test_clean_response_needs_no_correction(self, mock_openai):
        """A response with no issues should be returned as-is, iteration 1."""
        mock_openai.chat_completion = AsyncMock(return_value=_make_evaluate_response(issues_found=False))

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="De opzegtermijn bedraagt twee maanden (artikel 12).",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
            query="Wat is de opzegtermijn?",
        )

        assert isinstance(result, ReflectionResult)
        assert result.final_response == "De opzegtermijn bedraagt twee maanden (artikel 12)."
        assert result.corrections_made == 0
        assert result.original_issues == []
        # evaluate was called once, no correction call
        assert mock_openai.chat_completion.await_count == 1

    @pytest.mark.asyncio
    async def test_hallucinated_article_gets_corrected(self, mock_openai):
        """A response citing a non-existent article should trigger correction."""
        issues = [
            {
                "issue_type": "fabricated_reference",
                "description": "Artikel 99 does not appear in the provided context.",
                "severity": "high",
                "suggestion": "Remove the reference to Artikel 99.",
            },
        ]

        # First call: evaluation finds issues.
        # Second call: correction returns fixed text.
        # Third call: re-evaluation finds no issues.
        mock_openai.chat_completion = AsyncMock(
            side_effect=[
                _make_evaluate_response(issues_found=True, issues=issues),
                _make_correction_response("De opzegtermijn bedraagt twee maanden conform artikel 12."),
                _make_evaluate_response(issues_found=False),
            ]
        )

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="Volgens artikel 99 bedraagt de opzegtermijn twee maanden.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
            query="Wat is de opzegtermijn?",
        )

        assert result.corrections_made == 1
        assert result.final_response == ("De opzegtermijn bedraagt twee maanden conform artikel 12.")
        assert len(result.original_issues) == 1
        assert result.original_issues[0].issue_type == IssueType.FABRICATED_REFERENCE

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self, mock_openai):
        """The loop must not exceed max_iterations even if issues persist."""
        persistent_issues = [
            {
                "issue_type": "incomplete_answer",
                "description": "Answer does not address liability.",
                "severity": "medium",
                "suggestion": "Add liability information.",
            },
        ]

        # Every evaluation finds issues; every correction returns new text.
        mock_openai.chat_completion = AsyncMock(
            side_effect=[
                # Iteration 1: evaluate -> issues
                _make_evaluate_response(issues_found=True, issues=persistent_issues),
                # Iteration 1: correct
                _make_correction_response("Corrected attempt 1."),
                # Iteration 2: evaluate -> issues still
                _make_evaluate_response(issues_found=True, issues=persistent_issues),
                # Iteration 2: correct
                _make_correction_response("Corrected attempt 2."),
            ]
        )

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="Original incomplete response.",
            context="Context about liability.",
            query="Wie is aansprakelijk?",
        )

        assert result.iterations == 2
        assert result.final_response == "Corrected attempt 2."
        # 1 issue per iteration = 2 total
        assert result.corrections_made == 2

    @pytest.mark.asyncio
    async def test_llm_evaluation_failure_returns_original(self, mock_openai):
        """If the LLM evaluation call raises, the original response is returned.

        The ``_evaluate_response`` method has ``@retry(stop=stop_after_attempt(2))``
        so the mock will be called twice before the error propagates.
        """
        mock_openai.chat_completion = AsyncMock(side_effect=RuntimeError("Service unavailable"))

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="Original response that cannot be checked.",
            context="Some context.",
            query="Test query.",
        )

        # Should gracefully return the original response.
        assert result.final_response == "Original response that cannot be checked."
        assert result.corrections_made == 0

    @pytest.mark.asyncio
    async def test_llm_correction_failure_keeps_last_good_response(self, mock_openai):
        """If correction fails, the last known response is preserved.

        Evaluation succeeds (1 call), then the correction method retries
        twice (``stop_after_attempt(2)``) before propagating the error.
        """
        issues = [
            {
                "issue_type": "missing_citation",
                "description": "No article reference provided.",
                "severity": "medium",
                "suggestion": "Add article 12.",
            },
        ]

        mock_openai.chat_completion = AsyncMock(
            side_effect=[
                # evaluate call -> finds issues
                _make_evaluate_response(issues_found=True, issues=issues),
                # correction call attempt 1 -> fails
                RuntimeError("Correction call failed"),
                # correction call attempt 2 (tenacity retry) -> fails
                RuntimeError("Correction call failed"),
            ]
        )

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="Response without citation.",
            context="Artikel 12: Opzegtermijn is twee maanden.",
            query="Wat is de opzegtermijn?",
        )

        # Original response should be kept because correction failed.
        assert result.final_response == "Response without citation."
        assert len(result.original_issues) == 1

    @pytest.mark.asyncio
    async def test_multiple_issues_in_single_iteration(self, mock_openai):
        """Multiple issues detected at once should all be counted."""
        issues = [
            {
                "issue_type": "hallucinated_fact",
                "description": "Claim about 6-month notice is not in context.",
                "severity": "high",
                "suggestion": "Correct to 2 months.",
            },
            {
                "issue_type": "missing_citation",
                "description": "No article number referenced.",
                "severity": "medium",
                "suggestion": "Add reference to artikel 12.",
            },
        ]

        mock_openai.chat_completion = AsyncMock(
            side_effect=[
                _make_evaluate_response(issues_found=True, issues=issues),
                _make_correction_response("Corrected: twee maanden (artikel 12)."),
                _make_evaluate_response(issues_found=False),
            ]
        )

        engine = ReflectionEngine(openai_service=mock_openai, max_iterations=2)
        result = await engine.reflect_and_correct(
            response="De opzegtermijn is zes maanden.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
            query="Wat is de opzegtermijn?",
        )

        assert result.corrections_made == 2
        assert len(result.original_issues) == 2
        assert {i.issue_type for i in result.original_issues} == {
            IssueType.HALLUCINATED_FACT,
            IssueType.MISSING_CITATION,
        }

    @pytest.mark.asyncio
    async def test_result_includes_elapsed_seconds(self, mock_openai):
        """The result should record a positive elapsed time."""
        mock_openai.chat_completion = AsyncMock(return_value=_make_evaluate_response(issues_found=False))

        engine = ReflectionEngine(openai_service=mock_openai)
        result = await engine.reflect_and_correct(response="Fine response.", context="Context.", query="Query.")

        assert result.elapsed_seconds >= 0.0
