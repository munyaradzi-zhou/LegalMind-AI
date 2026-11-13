"""Self-reflection and correction loop for AI-generated legal responses.

Analyses AI responses for hallucinated facts, missing citations, incomplete
answers, and fabricated legal references.  Uses GPT-4o to evaluate and
optionally regenerate corrected responses up to a configurable maximum
number of iterations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.services import AzureOpenAIService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class IssueType(StrEnum):
    """Categories of issues detected during reflection."""

    HALLUCINATED_FACT = "hallucinated_fact"
    MISSING_CITATION = "missing_citation"
    INCOMPLETE_ANSWER = "incomplete_answer"
    FABRICATED_REFERENCE = "fabricated_reference"


@dataclass(slots=True)
class ReflectionIssue:
    """A single issue detected in an AI response.

    Attributes:
        issue_type: The category of the issue.
        description: Human-readable description of what was found.
        severity: Severity indicator (high / medium / low).
        suggestion: Suggested correction.
    """

    issue_type: IssueType
    description: str
    severity: str = "medium"
    suggestion: str = ""


@dataclass(slots=True)
class ReflectionResult:
    """The outcome of the self-reflection / correction loop.

    Attributes:
        final_response: The (possibly corrected) response text.
        iterations: Number of reflection iterations that were executed.
        corrections_made: Total number of corrections applied.
        original_issues: Issues detected in the *original* response
            (before any correction).
        all_issues: Flat list of issues across all iterations.
        elapsed_seconds: Wall-clock time for the full reflection loop.
    """

    final_response: str
    iterations: int = 0
    corrections_made: int = 0
    original_issues: list[ReflectionIssue] = field(default_factory=list)
    all_issues: list[ReflectionIssue] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_REFLECTION_SYSTEM_PROMPT = """\
You are a rigorous Dutch legal quality-assurance reviewer.  You receive:
1. The user's original query.
2. The retrieved context passages used to generate the answer.
3. The AI-generated response.

Your task is to identify problems in the response.  Check for:

- **Hallucinated facts**: claims not supported by the provided context.
- **Missing citations**: statements that should reference specific articles,
  sections, or document identifiers but do not.
- **Incomplete answers**: aspects of the user's question that are left
  unanswered when the context contains the information.
- **Fabricated legal references**: article numbers, case references (ECLI),
  or statute citations that do not appear in the context and cannot be
  verified.

Respond with a JSON object:
{
  "issues_found": true | false,
  "issues": [
    {
      "issue_type": "hallucinated_fact" | "missing_citation" | "incomplete_answer" | "fabricated_reference",
      "description": "...",
      "severity": "high" | "medium" | "low",
      "suggestion": "..."
    }
  ]
}

If the response is accurate and complete, set ``issues_found`` to false and
return an empty ``issues`` list.  Return ONLY valid JSON.
"""

_CORRECTION_SYSTEM_PROMPT = """\
You are a Dutch legal AI assistant.  You previously generated a response that
contained issues identified by a quality reviewer.

You are given:
1. The user's original query.
2. The retrieved context passages.
3. Your previous (flawed) response.
4. The list of issues found.

Generate a corrected response that:
- Fixes every identified issue.
- Only states facts that are supported by the provided context.
- Includes proper citations (article numbers, document titles, ECLI numbers)
  wherever applicable.
- Answers the user's question as completely as the context allows.
- Uses formal Dutch legal language.

Return ONLY the corrected response text.
"""


# ---------------------------------------------------------------------------
# Reflection engine
# ---------------------------------------------------------------------------


class ReflectionEngine:
    """Self-reflection and iterative correction for AI-generated responses.

    Wraps :class:`AzureOpenAIService` to perform an evaluate-then-correct
    loop.  The engine first asks GPT-4o to critique a response, and if
    issues are found, it generates a corrected version.  The loop runs
    for at most ``max_iterations`` rounds (default 2).

    Args:
        openai_service: Pre-configured OpenAI service instance.  If *None*,
            a new instance is created.
        max_iterations: Maximum number of reflection/correction cycles.
    """

    def __init__(
        self,
        *,
        openai_service: AzureOpenAIService | None = None,
        max_iterations: int = 2,
    ) -> None:
        self._openai = openai_service or AzureOpenAIService()
        self._max_iterations = max(1, max_iterations)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reflect_and_correct(
        self,
        response: str,
        context: str,
        query: str,
    ) -> ReflectionResult:
        """Run the reflection/correction loop.

        Args:
            response: The AI-generated response to evaluate.
            context: The concatenated retrieval context used to generate
                the response.
            query: The original user query.

        Returns:
            A :class:`ReflectionResult` with the (possibly corrected)
            response and diagnostic metadata.
        """
        start_time = time.monotonic()

        logger.info(
            "reflection_start",
            query_length=len(query),
            response_length=len(response),
            context_length=len(context),
            max_iterations=self._max_iterations,
        )

        current_response = response
        all_issues: list[ReflectionIssue] = []
        original_issues: list[ReflectionIssue] = []
        total_corrections = 0
        iteration = 0

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("reflection_iteration", iteration=iteration)

            try:
                issues = await self._evaluate_response(current_response, context, query)
            except Exception:
                logger.exception(
                    "reflection_evaluate_error",
                    iteration=iteration,
                )
                # On evaluation failure, return what we have so far.
                break

            if iteration == 1:
                original_issues = list(issues)

            all_issues.extend(issues)

            if not issues:
                logger.info(
                    "reflection_no_issues",
                    iteration=iteration,
                )
                break

            logger.info(
                "reflection_issues_found",
                iteration=iteration,
                issue_count=len(issues),
                types=[i.issue_type for i in issues],
            )

            # Attempt correction
            try:
                current_response = await self._generate_correction(current_response, context, query, issues)
                total_corrections += len(issues)
            except Exception:
                logger.exception(
                    "reflection_correction_error",
                    iteration=iteration,
                )
                # On correction failure, keep the last good response.
                break

        elapsed = time.monotonic() - start_time

        result = ReflectionResult(
            final_response=current_response,
            iterations=iteration,
            corrections_made=total_corrections,
            original_issues=original_issues,
            all_issues=all_issues,
            elapsed_seconds=round(elapsed, 3),
        )

        logger.info(
            "reflection_complete",
            iterations=result.iterations,
            corrections_made=result.corrections_made,
            original_issue_count=len(result.original_issues),
            elapsed=result.elapsed_seconds,
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def _evaluate_response(
        self,
        response: str,
        context: str,
        query: str,
    ) -> list[ReflectionIssue]:
        """Ask the LLM to critique the response and return issues."""
        import json as _json

        user_content = f"## Original Query\n{query}\n\n## Retrieved Context\n{context}\n\n## AI Response\n{response}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _REFLECTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        raw = await self._openai.chat_completion(
            messages,
            temperature=0.1,
            max_tokens=2048,
        )

        content = raw["choices"][0]["message"]["content"].strip()

        parsed = _json.loads(content)

        if not parsed.get("issues_found", False):
            return []

        issues: list[ReflectionIssue] = []
        for item in parsed.get("issues", []):
            try:
                issues.append(
                    ReflectionIssue(
                        issue_type=IssueType(item["issue_type"]),
                        description=item.get("description", ""),
                        severity=item.get("severity", "medium"),
                        suggestion=item.get("suggestion", ""),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "reflection_issue_parse_skip",
                    raw_item=str(item)[:200],
                    error=str(exc),
                )
        return issues

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def _generate_correction(
        self,
        response: str,
        context: str,
        query: str,
        issues: list[ReflectionIssue],
    ) -> str:
        """Generate a corrected response based on identified issues."""
        import json as _json

        issues_text = _json.dumps(
            [
                {
                    "issue_type": i.issue_type.value,
                    "description": i.description,
                    "severity": i.severity,
                    "suggestion": i.suggestion,
                }
                for i in issues
            ],
            ensure_ascii=False,
            indent=2,
        )

        user_content = (
            f"## Original Query\n{query}\n\n"
            f"## Retrieved Context\n{context}\n\n"
            f"## Previous Response\n{response}\n\n"
            f"## Issues Found\n{issues_text}"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _CORRECTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        raw = await self._openai.chat_completion(
            messages,
            temperature=0.2,
            max_tokens=4096,
        )

        corrected = str(raw["choices"][0]["message"]["content"]).strip()

        logger.info(
            "reflection_correction_generated",
            corrected_length=len(corrected),
        )

        return corrected
