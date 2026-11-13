"""LegalMind AI multi-agent framework.

Provides specialist agents for Dutch legal document analysis, research, drafting,
and compliance checking, orchestrated by an intent-based router. Built on
Semantic Kernel with Azure OpenAI (GPT-4o) for chat completions and tool calling.

Architecture::

    User message
         │
         ▼
    ┌─────────────┐
    │ Orchestrator │  ← GPT-4o intent classification
    └──────┬──────┘
           │ routes to
     ┌─────┼──────────┬──────────────┐
     ▼     ▼          ▼              ▼
  Document  Research  Drafting  Compliance
   Agent     Agent     Agent      Agent
     │        │         │           │
     └────────┴─────────┴───────────┘
              │
         Tool Plugins
    (search, extract, summarize,
     translate, draft)

Usage::

    from legalmind.agents import Orchestrator

    orchestrator = Orchestrator()
    response = await orchestrator.process_message("Vat dit contract samen.")
    print(response.agent_response.content)
"""

from legalmind.agents.base_agent import AgentResponse, BaseAgent
from legalmind.agents.compliance_agent import ComplianceAgent
from legalmind.agents.document_agent import DocumentAgent
from legalmind.agents.drafting_agent import DraftingAgent
from legalmind.agents.orchestrator import Intent, Orchestrator, OrchestratorResponse, RoutingDecision
from legalmind.agents.reflection import ReflectionEngine, ReflectionResult
from legalmind.agents.research_agent import ResearchAgent

__all__ = [
    # Core
    "AgentResponse",
    "BaseAgent",
    # Specialist agents
    "ComplianceAgent",
    "DocumentAgent",
    "DraftingAgent",
    # Orchestrator
    "Intent",
    "Orchestrator",
    "OrchestratorResponse",
    # Reflection
    "ReflectionEngine",
    "ReflectionResult",
    "ResearchAgent",
    "RoutingDecision",
]
