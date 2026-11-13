"""Unit tests for Responsible AI guardrails."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from legalmind.context.guardrails import (
    GuardrailResult,
    GuardrailVerdict,
    ResponsibleAIGuardrails,
)
from legalmind.services.content_safety import (
    ContentSafetyResult,
    HarmCategory,
    HarmScore,
    PiiDetectionResult,
    PiiEntity,
    SafetyAction,
)


def _safe_result() -> ContentSafetyResult:
    return ContentSafetyResult(
        is_safe=True,
        action=SafetyAction.ALLOW,
        harm_scores=[HarmScore(category=HarmCategory.HATE, severity=0)],
        flagged_categories=[],
    )


def _blocked_result() -> ContentSafetyResult:
    return ContentSafetyResult(
        is_safe=False,
        action=SafetyAction.BLOCK,
        harm_scores=[HarmScore(category=HarmCategory.VIOLENCE, severity=6)],
        flagged_categories=["Violence"],
    )


def _no_pii() -> PiiDetectionResult:
    return PiiDetectionResult(entities=[], entity_count=0, categories_found=[], has_pii=False)


def _with_pii() -> PiiDetectionResult:
    return PiiDetectionResult(
        entities=[PiiEntity(text="Jan", category="Person", subcategory=None, confidence=0.9, offset=0, length=3)],
        entity_count=1,
        categories_found=["Person"],
        has_pii=True,
    )


@pytest.fixture
def mock_safety_service():
    service = AsyncMock()
    service.analyze_text = AsyncMock(return_value=_safe_result())
    service.detect_pii = AsyncMock(return_value=_no_pii())
    return service


@pytest.fixture
def mock_openai_service():
    service = AsyncMock()
    grounded_json = '{"score": 0.9, "is_grounded": true, "ungrounded_claims": [], "explanation": "Fully grounded"}'
    service.chat_completion = AsyncMock(
        return_value={
            "choices": [{"message": {"content": grounded_json}}],
        }
    )
    return service


@pytest.fixture
def guardrails(mock_safety_service, mock_openai_service):
    return ResponsibleAIGuardrails(
        content_safety_service=mock_safety_service,
        openai_service=mock_openai_service,
    )


class TestInputGuardrails:
    """Tests for pre-flight input checking."""

    @pytest.mark.asyncio
    async def test_safe_input_passes(self, guardrails: ResponsibleAIGuardrails):
        result = await guardrails.check_input("Wat zijn de opzegvoorwaarden?")
        assert result.verdict == GuardrailVerdict.PASS
        assert result.blocked_reason == ""

    @pytest.mark.asyncio
    async def test_harmful_input_blocked(self, guardrails: ResponsibleAIGuardrails, mock_safety_service):
        mock_safety_service.analyze_text.return_value = _blocked_result()
        result = await guardrails.check_input("harmful content")
        assert result.verdict == GuardrailVerdict.BLOCK
        assert result.blocked_reason != ""

    @pytest.mark.asyncio
    async def test_input_with_pii_warns(self, guardrails: ResponsibleAIGuardrails, mock_safety_service):
        mock_safety_service.detect_pii.return_value = _with_pii()
        result = await guardrails.check_input("Jan de Vries wil zijn polis opzeggen")
        assert result.verdict == GuardrailVerdict.WARN
        assert len(result.warnings) >= 1
        assert result.pii_detection is not None
        assert result.pii_detection.has_pii


class TestOutputGuardrails:
    """Tests for post-flight output checking."""

    @pytest.mark.asyncio
    async def test_safe_grounded_output_passes(self, guardrails: ResponsibleAIGuardrails):
        result = await guardrails.check_output(
            response="Op basis van artikel 12 bedraagt de opzegtermijn twee maanden.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
        )
        assert result.verdict == GuardrailVerdict.PASS

    @pytest.mark.asyncio
    async def test_harmful_output_blocked(self, guardrails: ResponsibleAIGuardrails, mock_safety_service):
        mock_safety_service.analyze_text.return_value = _blocked_result()
        result = await guardrails.check_output(
            response="harmful content",
            context="clean context",
        )
        assert result.verdict == GuardrailVerdict.BLOCK

    @pytest.mark.asyncio
    async def test_ungrounded_output_warns(self, guardrails: ResponsibleAIGuardrails, mock_openai_service):
        ungrounded_json = (
            '{"score": 0.3, "is_grounded": false,'
            ' "ungrounded_claims": ["Fabricated article 99"],'
            ' "explanation": "Hallucinated reference"}'
        )
        mock_openai_service.chat_completion.return_value = {
            "choices": [{"message": {"content": ungrounded_json}}],
        }
        result = await guardrails.check_output(
            response="Volgens artikel 99 is de premie ongeldig.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
        )
        assert result.verdict == GuardrailVerdict.WARN
        assert result.groundedness is not None
        assert not result.groundedness.is_grounded
        assert len(result.groundedness.ungrounded_claims) >= 1


class TestGroundednessCheck:
    """Tests for groundedness verification."""

    @pytest.mark.asyncio
    async def test_question_is_given_to_the_judge(self, guardrails: ResponsibleAIGuardrails, mock_openai_service):
        # A fact from the wrong document (another policy's premium) is only detectable
        # when the judge knows what was asked.
        await guardrails.check_groundedness(
            response="De premie van de reisverzekering bedraagt €180,00 per jaar [1].",
            context="[1] Rechtsbijstandpolis, artikel 6.1: De jaarpremie bedraagt €180,00.",
            question="Hoeveel bedraagt de premie van de reisverzekering?",
        )
        prompt = mock_openai_service.chat_completion.call_args.kwargs["messages"][0]["content"]
        assert "Hoeveel bedraagt de premie van de reisverzekering?" in prompt
        assert "different document, policy, case or person" in prompt

    @pytest.mark.asyncio
    async def test_facts_about_another_subject_are_not_grounded(
        self, guardrails: ResponsibleAIGuardrails, mock_openai_service
    ):
        # The judge sees the claim in the context (score 0.9) but recognises the question is
        # about a policy the documents don't contain: the answer must not count as grounded.
        verdict = (
            '{"question_subject": "de premie van de reisverzekering", "subject_in_context": false,'
            ' "answers_with_other_subject": true, "score": 0.9, "is_grounded": true,'
            ' "ungrounded_claims": [], "used_sources": [1],'
            ' "explanation": "EUR 180 is the premium of the legal expenses policy"}'
        )
        mock_openai_service.chat_completion.return_value = {"choices": [{"message": {"content": verdict}}]}
        result = await guardrails.check_groundedness(
            response="De premie van de reisverzekering bedraagt €180,00 per jaar [1].",
            context="[1] Rechtsbijstandpolis, artikel 6.1: De jaarpremie bedraagt €180,00.",
            question="Hoeveel bedraagt de premie van de reisverzekering?",
        )
        assert not result.is_grounded
        assert result.score <= 0.2
        assert result.used_sources == [1]
        assert "reisverzekering" in result.ungrounded_claims[-1]

    @pytest.mark.asyncio
    async def test_declining_about_a_missing_subject_is_grounded(
        self, guardrails: ResponsibleAIGuardrails, mock_openai_service
    ):
        verdict = (
            '{"question_subject": "de premie van de reisverzekering", "subject_in_context": false,'
            ' "score": 1.0, "is_grounded": true, "ungrounded_claims": [], "used_sources": [],'
            ' "explanation": "Correctly says the documents do not cover it"}'
        )
        mock_openai_service.chat_completion.return_value = {"choices": [{"message": {"content": verdict}}]}
        result = await guardrails.check_groundedness(
            response="De documenten bevatten geen informatie over een reisverzekering.",
            context="[1] Rechtsbijstandpolis, artikel 6.1: De jaarpremie bedraagt €180,00.",
            question="Hoeveel bedraagt de premie van de reisverzekering?",
        )
        assert result.is_grounded
        assert result.used_sources == []

    @pytest.mark.asyncio
    async def test_declining_while_naming_the_other_subject_is_grounded(
        self, guardrails: ResponsibleAIGuardrails, mock_openai_service
    ):
        # "Not in the documents; the waiting period there is for the legal expenses policy [1]."
        verdict = (
            '{"question_subject": "de wachttijd van de reisverzekering", "subject_in_context": false,'
            ' "answers_with_other_subject": false, "score": 1.0, "is_grounded": true,'
            ' "ungrounded_claims": [], "used_sources": [1], "explanation": "Declines, attributes correctly"}'
        )
        mock_openai_service.chat_completion.return_value = {"choices": [{"message": {"content": verdict}}]}
        result = await guardrails.check_groundedness(
            response="Geen informatie over een reisverzekering; de wachttijd van 3 maanden [1] "
            "geldt voor de rechtsbijstandverzekering.",
            context="[1] Rechtsbijstandpolis, artikel 8.1: wachttijd van 3 maanden.",
            question="Welke wachttijd geldt voor de reisverzekering?",
        )
        assert result.is_grounded
        assert result.used_sources == [1]

    @pytest.mark.asyncio
    async def test_grounded_response(self, guardrails: ResponsibleAIGuardrails):
        result = await guardrails.check_groundedness(
            response="De opzegtermijn is twee maanden.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
        )
        assert result.is_grounded
        assert result.score >= 0.6

    @pytest.mark.asyncio
    async def test_ungrounded_response(self, guardrails: ResponsibleAIGuardrails, mock_openai_service):
        hallucinated_json = (
            '{"score": 0.2, "is_grounded": false,'
            ' "ungrounded_claims": ["Non-existent article"],'
            ' "explanation": "Hallucinated"}'
        )
        mock_openai_service.chat_completion.return_value = {
            "choices": [{"message": {"content": hallucinated_json}}],
        }
        result = await guardrails.check_groundedness(
            response="Volgens artikel 999 moet u betalen.",
            context="Artikel 12: De opzegtermijn bedraagt twee maanden.",
        )
        assert not result.is_grounded
        assert result.score < 0.6

    @pytest.mark.asyncio
    async def test_groundedness_error_is_reported_as_unverified(
        self, guardrails: ResponsibleAIGuardrails, mock_openai_service
    ):
        mock_openai_service.chat_completion.side_effect = Exception("LLM unavailable")
        result = await guardrails.check_groundedness(
            response="Some response",
            context="Some context",
        )
        # A failed check must not be reported as a pass
        assert not result.verified
        assert not result.is_grounded

    @pytest.mark.asyncio
    async def test_groundedness_parses_fenced_json(self, guardrails: ResponsibleAIGuardrails, mock_openai_service):
        fenced = '```json\n{"score": 0.95, "is_grounded": true, "ungrounded_claims": []}\n```'
        mock_openai_service.chat_completion.return_value = {"choices": [{"message": {"content": fenced}}]}
        result = await guardrails.check_groundedness(response="r", context="c")
        assert result.verified
        assert result.is_grounded
        assert result.score == 0.95

    @pytest.mark.asyncio
    async def test_groundedness_requests_json_mode(self, guardrails: ResponsibleAIGuardrails, mock_openai_service):
        mock_openai_service.chat_completion.return_value = {
            "choices": [{"message": {"content": '{"score": 0.9, "ungrounded_claims": []}'}}]
        }
        await guardrails.check_groundedness(response="r", context="c")
        assert mock_openai_service.chat_completion.call_args.kwargs["response_format"] == {"type": "json_object"}


class TestGuardrailResult:
    """Tests for GuardrailResult data model."""

    def test_pass_verdict(self):
        result = GuardrailResult(verdict=GuardrailVerdict.PASS)
        assert result.verdict == GuardrailVerdict.PASS
        assert result.warnings == []
        assert result.blocked_reason == ""

    def test_block_verdict_has_reason(self):
        result = GuardrailResult(
            verdict=GuardrailVerdict.BLOCK,
            blocked_reason="Content policy violation",
        )
        assert result.verdict == GuardrailVerdict.BLOCK
        assert result.blocked_reason == "Content policy violation"

    def test_warn_verdict_has_warnings(self):
        result = GuardrailResult(
            verdict=GuardrailVerdict.WARN,
            warnings=["PII detected", "Low groundedness"],
        )
        assert len(result.warnings) == 2
