"""Integration tests for multi-agent orchestration.

Requires Azure credentials for OpenAI and search services.
Run with: pytest tests/integration/ -m integration
"""

from __future__ import annotations

import pytest

from legalmind.agents.orchestrator import Orchestrator


@pytest.mark.integration
class TestAgentOrchestration:
    """End-to-end tests for multi-agent routing and execution."""

    @pytest.mark.asyncio
    async def test_routes_to_document_agent(self):
        """Document analysis questions should route to document_analysis."""
        orchestrator = Orchestrator()
        response = await orchestrator.process_message(
            "Vat dit contract samen in 5 punten",
            additional_context={"retrieved_documents": "Artikel 1: Definities..."},
        )
        assert response.agent_response.agent_name in ("document_analysis", "orchestrator")
        assert len(response.agent_response.content) > 0

    @pytest.mark.asyncio
    async def test_routes_to_research_agent(self):
        """Legal research questions should route to legal_research."""
        orchestrator = Orchestrator()
        response = await orchestrator.process_message(
            "Zoek relevante jurisprudentie over aansprakelijkheid bij arbeidsongeval"
        )
        assert response.agent_response.agent_name in ("legal_research", "orchestrator")
        assert len(response.agent_response.content) > 0

    @pytest.mark.asyncio
    async def test_routes_to_drafting_agent(self):
        """Drafting requests should route to drafting."""
        orchestrator = Orchestrator()
        response = await orchestrator.process_message("Schrijf een brief aan de tegenpartij over de schadeclaim")
        assert response.agent_response.agent_name in ("drafting", "orchestrator")
        assert len(response.agent_response.content) > 0

    @pytest.mark.asyncio
    async def test_agent_list(self):
        """Should list all available agents."""
        orchestrator = Orchestrator()
        agents = orchestrator.list_agents()
        assert len(agents) >= 4
        agent_names = [a["name"] for a in agents]
        assert "document_analysis" in agent_names
        assert "legal_research" in agent_names
