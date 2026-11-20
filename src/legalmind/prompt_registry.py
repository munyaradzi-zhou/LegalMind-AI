"""Versions of every prompt, from their content, so each result can be traced to its prompts.

A prompt's version is the first 12 hex characters of the SHA-256 of its text: any edit
changes it, and nobody has to remember to bump a number. The fingerprint is one hash over
all versions; `/health` reports it, evaluation reports record it next to their numbers, and
the evaluation gate shows when a pull request changed a prompt.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from typing import Any


def _sources() -> dict[str, Any]:
    # Imported here so that importing this module stays cheap
    from legalmind.agents import reflection
    from legalmind.agents.prompts import few_shot_examples, prompt_templates, system_prompts
    from legalmind.agents.tools import draft_tool
    from legalmind.context import conversation, guardrails
    from legalmind.ingestion import document_context
    from legalmind.retrieval import query_rewriter

    return {
        "orchestrator": system_prompts.ORCHESTRATOR_PROMPT,
        "agent.document": system_prompts.DOCUMENT_AGENT_PROMPT,
        "agent.research": system_prompts.RESEARCH_AGENT_PROMPT,
        "agent.drafting": system_prompts.DRAFTING_AGENT_PROMPT,
        "agent.compliance": system_prompts.COMPLIANCE_AGENT_PROMPT,
        "few_shot.document": few_shot_examples.DOCUMENT_FEW_SHOT_EXAMPLES,
        "few_shot.research": few_shot_examples.RESEARCH_FEW_SHOT_EXAMPLES,
        "few_shot.drafting": few_shot_examples.DRAFTING_FEW_SHOT_EXAMPLES,
        "few_shot.compliance": few_shot_examples.COMPLIANCE_FEW_SHOT_EXAMPLES,
        "template.case_analysis": prompt_templates.CASE_ANALYSIS_TEMPLATE,
        "template.document_comparison": prompt_templates.DOCUMENT_COMPARISON_TEMPLATE,
        "template.legal_qa": prompt_templates.LEGAL_QA_TEMPLATE,
        "template.drafting": prompt_templates.DRAFTING_TEMPLATE,
        "reflection": reflection._REFLECTION_SYSTEM_PROMPT,
        "reflection.correction": reflection._CORRECTION_SYSTEM_PROMPT,
        "tool.draft": draft_tool._DRAFT_SYSTEM_PROMPT,
        "conversation.summary": conversation.SUMMARIZATION_PROMPT,
        "guardrail.groundedness": guardrails._GROUNDEDNESS_PROMPT,
        "ingestion.document_context": document_context._DESCRIBE_PROMPT,
        "retrieval.rewrite": query_rewriter._REWRITE_SYSTEM_PROMPT,
        "retrieval.decompose": query_rewriter._DECOMPOSE_SYSTEM_PROMPT,
        "retrieval.hyde": query_rewriter._HYDE_SYSTEM_PROMPT,
    }


def _hash(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@cache
def prompt_versions() -> dict[str, str]:
    """Name -> version (12 hex characters of the content hash) for every prompt."""
    return {name: _hash(value) for name, value in sorted(_sources().items())}


@cache
def prompt_fingerprint() -> str:
    """One version for the whole prompt set."""
    return _hash(prompt_versions())
