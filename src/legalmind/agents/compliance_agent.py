"""Regulatory Compliance specialist agent.

Checks legal documents, processes, and situations against Dutch and EU
regulatory requirements. Uses the SearchTool (for finding applicable
regulations) and ExtractTool (for identifying entities and references).
"""

from __future__ import annotations

from typing import Any

from legalmind.agents.base_agent import BaseAgent
from legalmind.agents.prompts.few_shot_examples import COMPLIANCE_FEW_SHOT_EXAMPLES
from legalmind.agents.prompts.system_prompts import COMPLIANCE_AGENT_PROMPT
from legalmind.agents.tools.extract_tool import ExtractTool
from legalmind.agents.tools.search_tool import SearchTool


class ComplianceAgent(BaseAgent):
    """Specialist agent for regulatory compliance checking.

    Focus areas:
    - AVG/GDPR data protection compliance
    - Wwft anti-money laundering requirements
    - Wft financial supervision regulations
    - Consumer protection (consumentenrecht)
    - Employment law (arbeidsrecht) compliance
    - Environmental regulations (Omgevingswet)
    - Corporate governance (BV/NV requirements)
    - Insurance regulations (relevant to legal expenses insurers)

    Capabilities:
    - Systematic compliance audits against applicable regulations
    - Risk identification with severity ratings
    - Gap analysis for missing compliance requirements
    - Remediation advice with specific corrections
    - Regulatory mapping (document sections to requirements)

    Tools:
    - ``search_tool``: Hybrid search for finding applicable regulations
    - ``extract_tool``: NER to identify regulated entities and references
    """

    def __init__(
        self,
        *,
        search_tool: SearchTool | None = None,
        extract_tool: ExtractTool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Compliance agent.

        Args:
            search_tool: Optional pre-configured SearchTool instance.
            extract_tool: Optional pre-configured ExtractTool instance.
            **kwargs: Additional arguments passed to BaseAgent.
        """
        self._search_tool = search_tool or SearchTool()
        self._extract_tool = extract_tool or ExtractTool()
        super().__init__(**kwargs)

        # Inject few-shot examples into history for better output quality
        for example in COMPLIANCE_FEW_SHOT_EXAMPLES:
            self.add_context_message(role=example["role"], content=example["content"])

    @property
    def name(self) -> str:
        return "compliance"

    @property
    def description(self) -> str:
        return (
            "Checks regulatory compliance — AVG/GDPR, Wwft, Wft, consumer protection, "
            "employment law, and other Dutch/EU regulations. Identifies violations and "
            "suggests remediation."
        )

    @property
    def system_prompt(self) -> str:
        return COMPLIANCE_AGENT_PROMPT

    def _register_plugins(self) -> None:
        """Register search and extraction tools as Semantic Kernel plugins."""
        self._kernel.add_plugin(self._search_tool, plugin_name="search_tool")
        self._kernel.add_plugin(self._extract_tool, plugin_name="extract_tool")
