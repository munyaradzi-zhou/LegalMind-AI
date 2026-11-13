"""Prompt definitions for LegalMind AI agents.

Provides system prompts, few-shot examples, and Jinja2-based prompt templates
for dynamic context injection across all specialist agents.
"""

from legalmind.agents.prompts.few_shot_examples import (
    COMPLIANCE_FEW_SHOT_EXAMPLES,
    DOCUMENT_FEW_SHOT_EXAMPLES,
    DRAFTING_FEW_SHOT_EXAMPLES,
    RESEARCH_FEW_SHOT_EXAMPLES,
)
from legalmind.agents.prompts.prompt_templates import (
    CASE_ANALYSIS_TEMPLATE,
    DOCUMENT_COMPARISON_TEMPLATE,
    DRAFTING_TEMPLATE,
    LEGAL_QA_TEMPLATE,
    render_template,
)
from legalmind.agents.prompts.system_prompts import (
    COMPLIANCE_AGENT_PROMPT,
    DOCUMENT_AGENT_PROMPT,
    DRAFTING_AGENT_PROMPT,
    ORCHESTRATOR_PROMPT,
    RESEARCH_AGENT_PROMPT,
)

__all__ = [
    # Prompt templates
    "CASE_ANALYSIS_TEMPLATE",
    "COMPLIANCE_AGENT_PROMPT",
    "COMPLIANCE_FEW_SHOT_EXAMPLES",
    "DOCUMENT_AGENT_PROMPT",
    "DOCUMENT_COMPARISON_TEMPLATE",
    # Few-shot examples
    "DOCUMENT_FEW_SHOT_EXAMPLES",
    "DRAFTING_AGENT_PROMPT",
    "DRAFTING_FEW_SHOT_EXAMPLES",
    "DRAFTING_TEMPLATE",
    "LEGAL_QA_TEMPLATE",
    # System prompts
    "ORCHESTRATOR_PROMPT",
    "RESEARCH_AGENT_PROMPT",
    "RESEARCH_FEW_SHOT_EXAMPLES",
    "render_template",
]
