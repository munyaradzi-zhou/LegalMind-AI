"""Unit tests for prompt templates and system prompts."""

from __future__ import annotations

import pytest

from legalmind.agents.prompts.prompt_templates import (
    CASE_ANALYSIS_TEMPLATE,
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


class TestSystemPrompts:
    """Tests for system prompt definitions."""

    def test_orchestrator_prompt_exists(self):
        assert len(ORCHESTRATOR_PROMPT) > 100
        assert "intent" in ORCHESTRATOR_PROMPT.lower() or "route" in ORCHESTRATOR_PROMPT.lower()

    def test_document_agent_prompt_exists(self):
        assert len(DOCUMENT_AGENT_PROMPT) > 100
        assert "document" in DOCUMENT_AGENT_PROMPT.lower()

    def test_research_agent_prompt_exists(self):
        assert len(RESEARCH_AGENT_PROMPT) > 100
        assert "research" in RESEARCH_AGENT_PROMPT.lower() or "onderzoek" in RESEARCH_AGENT_PROMPT.lower()

    def test_drafting_agent_prompt_has_dutch(self):
        """Drafting agent should mention Dutch language."""
        assert len(DRAFTING_AGENT_PROMPT) > 100
        assert "dutch" in DRAFTING_AGENT_PROMPT.lower() or "nederlands" in DRAFTING_AGENT_PROMPT.lower()

    def test_compliance_agent_prompt_exists(self):
        assert len(COMPLIANCE_AGENT_PROMPT) > 100

    def test_all_prompts_are_different(self):
        """Each agent should have a unique system prompt."""
        prompts = [
            DOCUMENT_AGENT_PROMPT,
            RESEARCH_AGENT_PROMPT,
            DRAFTING_AGENT_PROMPT,
            COMPLIANCE_AGENT_PROMPT,
        ]
        assert len(set(prompts)) == len(prompts)


class TestPromptTemplates:
    """Tests for Jinja2 prompt templates."""

    def test_legal_qa_template(self):
        result = render_template(
            LEGAL_QA_TEMPLATE,
            question="Wat is de opzegtermijn?",
            context_documents=[
                {
                    "title": "Polisvoorwaarden",
                    "content": "Artikel 12: De opzegtermijn is twee maanden.",
                }
            ],
        )
        assert "opzegtermijn" in result
        assert "Artikel 12" in result

    def test_case_analysis_template(self):
        result = render_template(
            CASE_ANALYSIS_TEMPLATE,
            case_number="2024-001",
            case_type="Arbeidsrecht",
            client_name="Jan de Vries",
            facts="Arbeidsongeval op de werkplek",
        )
        assert "Arbeidsongeval" in result

    def test_template_renders_without_optional_vars(self):
        """Templates with optional variables should render without error."""
        # CASE_ANALYSIS_TEMPLATE has many optional vars; only required ones provided
        result = render_template(
            CASE_ANALYSIS_TEMPLATE,
            case_number="2024-999",
            case_type="Test",
            client_name="Test Client",
            facts="Test facts",
        )
        assert "2024-999" in result

    def test_template_invalid_syntax_raises(self):
        """Invalid Jinja2 syntax should raise a ValueError."""
        with pytest.raises(ValueError):
            render_template("{{ {% broken %} }}")
