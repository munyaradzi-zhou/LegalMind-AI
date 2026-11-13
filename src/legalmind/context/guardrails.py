"""Responsible AI guardrails for input validation and output verification.

Provides pre-flight (input) and post-flight (output) safety checks for the
LegalMind AI chat pipeline, including:

- Content safety analysis (harmful content detection)
- PII detection and optional redaction
- Groundedness verification (LLM responses grounded in retrieved context)
- Audit trail for all safety decisions
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from legalmind.services.azure_openai import AzureOpenAIService
from legalmind.services.content_safety import (
    ContentSafetyResult,
    ContentSafetyService,
    PiiDetectionResult,
    SafetyAction,
)

logger = structlog.get_logger(__name__)


# ─── Data models ─────────────────────────────────────────────────────────────


class GuardrailVerdict(StrEnum):
    """Overall verdict from a guardrail check."""

    PASS = "pass"  # noqa: S105  # nosec B105 — enum value, not a password
    WARN = "warn"
    BLOCK = "block"


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating code fences and surrounding text."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end < start:
        msg = "No JSON object in model output"
        raise ValueError(msg)
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        msg = "Model output is not a JSON object"
        raise ValueError(msg)
    return parsed


@dataclass(frozen=True, slots=True)
class GroundednessResult:
    """Result of checking if a response is grounded in its context."""

    score: float  # 0.0-1.0 (1.0 = fully grounded)
    is_grounded: bool
    ungrounded_claims: list[str] = field(default_factory=list)
    explanation: str = ""
    verified: bool = True  # False when the check itself failed (no verdict available)
    used_sources: list[int] = field(default_factory=list)  # context numbers the response relies on


@dataclass(slots=True)
class GuardrailResult:
    """Combined result of all guardrail checks for a single message."""

    verdict: GuardrailVerdict
    content_safety: ContentSafetyResult | None = None
    pii_detection: PiiDetectionResult | None = None
    groundedness: GroundednessResult | None = None
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Groundedness Prompt ─────────────────────────────────────────────────────

_GROUNDEDNESS_PROMPT = """\
You are a legal AI quality auditor. Your task is to verify whether an AI-generated
response is fully grounded in the provided source context.

## User Question
{question}

## Source Context
{context}

## AI Response
{response}

## Instructions
First name the specific subject the question asks about (e.g. "the premium of the travel
insurance", "the medical costs of Mr De Vries' colleague") and decide whether the source
context contains information about THAT subject - the same type of policy, the same case,
the same person. A similar fact about something else does not count: the premium of a legal
expenses policy is not the premium of a travel insurance policy. A passage only counts for
the subject if the passage itself (its text, title or section) names that subject: if the
question asks about a "reisverzekering" and no passage mentions a reisverzekering, the subject
is not in the context, even when a passage gives a premium. Do not assume an unnamed document
is the one the question asks about, and do not infer that one party's policy covers another.

Then analyze the AI response:
1. Is every factual claim in the response supported by the source context?
2. Are there any hallucinated facts, made-up article numbers, or fabricated legal references?
3. Are citations accurate (do they match the source material)?
4. Is each claim supported for the subject the user asked about? A fact that the context
   states about a different document, policy, case or person, presented as the answer about
   the subject in the question, is NOT grounded and must be listed in ungrounded_claims.
5. Which numbered sources ([1], [2], ...) does the response actually rely on, whether it cites
   them by number, article, section or document name? Empty if the response only says the
   documents don't contain the answer.

Respond in JSON format:
{{
  "question_subject": "<the specific subject of the question>",
  "subject_in_context": <boolean: the context has information about that exact subject>,
  "answers_with_other_subject": <boolean: true if the response presents facts about a different
      policy, case or person AS the answer to the question; false if it says the documents don't
      cover the subject, even when it mentions such facts and says they concern something else>,
  "score": <float 0.0 to 1.0, where 1.0 means fully grounded>,
  "is_grounded": <boolean>,
  "ungrounded_claims": [<list of specific claims not supported by context>],
  "used_sources": [<numbers of the sources the response relies on>],
  "explanation": "<brief explanation of your assessment>"
}}

Be strict — for legal AI, any hallucinated legal reference or article number is a serious issue.
Respond ONLY with valid JSON, no other text.
"""


# ─── Guardrails Implementation ──────────────────────────────────────────────


class ResponsibleAIGuardrails:
    """Input/output guardrails for responsible AI in legal context.

    Integrates content safety, PII detection, and groundedness checking
    into a unified guardrail pipeline that wraps the chat endpoint.

    Usage::

        guardrails = ResponsibleAIGuardrails()

        # Pre-flight: check user input
        input_result = await guardrails.check_input(user_message)
        if input_result.verdict == GuardrailVerdict.BLOCK:
            return blocked_response(input_result.blocked_reason)

        # ... run RAG pipeline ...

        # Post-flight: check AI output
        output_result = await guardrails.check_output(ai_response, context)
    """

    def __init__(
        self,
        *,
        content_safety_service: ContentSafetyService | None = None,
        openai_service: AzureOpenAIService | None = None,
        enable_pii_detection: bool = True,
        enable_groundedness: bool = True,
        groundedness_threshold: float = 0.6,
    ) -> None:
        self._safety = content_safety_service or ContentSafetyService()
        self._openai = openai_service or AzureOpenAIService()
        self._enable_pii = enable_pii_detection
        self._enable_groundedness = enable_groundedness
        self._groundedness_threshold = groundedness_threshold

    # ─── Input Guardrails ────────────────────────────────────────────────

    async def check_input(self, user_message: str) -> GuardrailResult:
        """Pre-flight check on user input before processing.

        Performs:
        1. Content safety analysis (block harmful content)
        2. PII detection (log detected PII for audit trail)

        Args:
            user_message: The raw user message.

        Returns:
            GuardrailResult with verdict and details.
        """
        logger.info("guardrail_check_input", message_length=len(user_message))
        warnings: list[str] = []

        # 1. Content safety
        safety_result = await self._safety.analyze_text(user_message)

        if safety_result.action == SafetyAction.BLOCK:
            logger.warning("guardrail_input_blocked", categories=safety_result.flagged_categories)
            return GuardrailResult(
                verdict=GuardrailVerdict.BLOCK,
                content_safety=safety_result,
                blocked_reason="Uw bericht bevat inhoud die niet verwerkt kan worden. "
                "Pas uw vraag aan en probeer het opnieuw.",
            )

        if not safety_result.is_safe:
            warnings.append(f"Content flagged: {', '.join(safety_result.flagged_categories)}")

        # 2. PII detection (advisory — don't block, just log)
        pii_result = None
        if self._enable_pii:
            pii_result = await self._safety.detect_pii(user_message)
            if pii_result.has_pii:
                warnings.append(
                    f"PII detected ({pii_result.entity_count} entities): {', '.join(pii_result.categories_found)}"
                )
                logger.info(
                    "guardrail_pii_in_input",
                    entity_count=pii_result.entity_count,
                    categories=pii_result.categories_found,
                )

        verdict = GuardrailVerdict.WARN if warnings else GuardrailVerdict.PASS

        return GuardrailResult(
            verdict=verdict,
            content_safety=safety_result,
            pii_detection=pii_result,
            warnings=warnings,
        )

    # ─── Output Guardrails ───────────────────────────────────────────────

    async def check_output(
        self,
        response: str,
        context: str,
        question: str = "",
    ) -> GuardrailResult:
        """Post-flight check on AI-generated response.

        Performs:
        1. Content safety analysis (block harmful output)
        2. PII detection in output (warn if PII leaks into response)
        3. Groundedness verification (verify claims against context)

        Args:
            response: The AI-generated response text.
            context: The retrieved context used to generate the response.
            question: The user's question, so claims about the wrong subject are caught.

        Returns:
            GuardrailResult with verdict and details.
        """
        logger.info("guardrail_check_output", response_length=len(response))
        warnings: list[str] = []

        # 1. Content safety on output
        safety_result = await self._safety.analyze_text(response)

        if safety_result.action == SafetyAction.BLOCK:
            logger.warning("guardrail_output_blocked", categories=safety_result.flagged_categories)
            return GuardrailResult(
                verdict=GuardrailVerdict.BLOCK,
                content_safety=safety_result,
                blocked_reason="Het gegenereerde antwoord kon niet worden geleverd vanwege inhoudelijke beperkingen.",
            )

        if not safety_result.is_safe:
            warnings.append(f"Output flagged: {', '.join(safety_result.flagged_categories)}")

        # 2. PII detection on output
        pii_result = None
        if self._enable_pii:
            pii_result = await self._safety.detect_pii(response)
            if pii_result.has_pii:
                warnings.append(
                    f"PII in output ({pii_result.entity_count} entities): {', '.join(pii_result.categories_found)}"
                )

        # 3. Groundedness check
        groundedness = None
        if self._enable_groundedness and context.strip():
            groundedness = await self.check_groundedness(response, context, question)
            if not groundedness.verified:
                warnings.append("Groundedness could not be verified")
            elif not groundedness.is_grounded:
                warnings.append(
                    f"Low groundedness ({groundedness.score:.2f}): "
                    f"{len(groundedness.ungrounded_claims)} ungrounded claims"
                )

        verdict = GuardrailVerdict.WARN if warnings else GuardrailVerdict.PASS

        return GuardrailResult(
            verdict=verdict,
            content_safety=safety_result,
            pii_detection=pii_result,
            groundedness=groundedness,
            warnings=warnings,
        )

    # ─── Groundedness Verification ───────────────────────────────────────

    async def check_groundedness(self, response: str, context: str, question: str = "") -> GroundednessResult:
        """Verify that an AI response is grounded in the retrieved context.

        Uses the chat model to compare each claim in the response against the
        source context, flagging hallucinated facts, fabricated legal references
        and facts borrowed from a document about a different subject.

        Args:
            response: The AI-generated response.
            context: The retrieved source context.
            question: The user's question (optional).

        Returns:
            GroundednessResult with score and list of ungrounded claims.
        """
        logger.info("groundedness_check", response_length=len(response), context_length=len(context))

        prompt = _GROUNDEDNESS_PROMPT.format(
            question=question[:1000] or "(not provided)",
            context=context[:8000],  # Limit context to fit in prompt
            response=response[:4000],
        )

        try:
            result = await self._openai.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )

            content = result["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            score = float(parsed.get("score", 0.5))
            used_sources = sorted({int(n) for n in parsed.get("used_sources", []) if str(n).isdigit()})
            ungrounded_claims = list(parsed.get("ungrounded_claims", []))
            # The documents say nothing about what was asked, yet the answer presents facts from
            # them: those facts belong to something else (another policy, case or person).
            # A response that says "not in the documents; the X mentioned there concerns Y" is fine.
            if (
                question
                and parsed.get("subject_in_context") is False
                and parsed.get("answers_with_other_subject") is True
            ):
                score = min(score, 0.2)
                subject = parsed.get("question_subject") or "the subject of the question"
                ungrounded_claims.append(f"The documents contain no information about {subject}")
            is_grounded = score >= self._groundedness_threshold

            groundedness = GroundednessResult(
                score=score,
                is_grounded=is_grounded,
                ungrounded_claims=ungrounded_claims,
                explanation=parsed.get("explanation", ""),
                used_sources=used_sources,
            )

            logger.info(
                "groundedness_result",
                score=score,
                is_grounded=is_grounded,
                ungrounded_claims=len(groundedness.ungrounded_claims),
            )
            return groundedness

        except Exception:
            logger.exception("groundedness_check_error")
            # Don't pretend the answer was checked: it is delivered, but flagged as unverified.
            return GroundednessResult(
                score=0.0,
                is_grounded=False,
                verified=False,
                explanation="Groundedness check failed; the answer could not be verified.",
            )
