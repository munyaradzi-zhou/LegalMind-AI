"""Legal Research specialist agent.

Finds relevant case law, statutes, regulations, and legal commentary to
support legal professionals. Uses the SearchTool (for knowledge base
search) and SummarizeTool (for condensing long legal texts).
"""

from __future__ import annotations

from typing import Any

from legalmind.agents.base_agent import BaseAgent
from legalmind.agents.prompts.few_shot_examples import RESEARCH_FEW_SHOT_EXAMPLES
from legalmind.agents.prompts.system_prompts import RESEARCH_AGENT_PROMPT
from legalmind.agents.tools.search_tool import SearchTool
from legalmind.agents.tools.summarize_tool import SummarizeTool


class ResearchAgent(BaseAgent):
    """Specialist agent for Dutch legal research.

    Capabilities:
    - Search for relevant Dutch case law (jurisprudentie) with ECLI references
    - Look up applicable statutes (Burgerlijk Wetboek, Awb, AVG, etc.)
    - Find EU directives and regulations
    - Synthesize legal principles and apply them to case facts
    - Compare current situations against established precedents
    - Summarize lengthy court decisions or legislative texts

    Tools:
    - ``search_tool``: Hybrid search with dedicated case law and statute filters
    - ``summarize_tool``: Extractive summarization for condensing legal texts
    """

    def __init__(
        self,
        *,
        search_tool: SearchTool | None = None,
        summarize_tool: SummarizeTool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Legal Research agent.

        Args:
            search_tool: Optional pre-configured SearchTool instance.
            summarize_tool: Optional pre-configured SummarizeTool instance.
            **kwargs: Additional arguments passed to BaseAgent.
        """
        self._search_tool = search_tool or SearchTool()
        self._summarize_tool = summarize_tool or SummarizeTool()
        super().__init__(**kwargs)

        # Inject few-shot examples into history for better response quality
        for example in RESEARCH_FEW_SHOT_EXAMPLES:
            self.add_context_message(role=example["role"], content=example["content"])

    @property
    def name(self) -> str:
        return "legal_research"

    @property
    def description(self) -> str:
        return (
            "Researches Dutch and EU law — finds case law, statutes, regulations, "
            "and legal precedents with full citations and analysis."
        )

    @property
    def system_prompt(self) -> str:
        return RESEARCH_AGENT_PROMPT

    def _register_plugins(self) -> None:
        """Register search and summarization tools as Semantic Kernel plugins."""
        self._kernel.add_plugin(self._search_tool, plugin_name="search_tool")
        self._kernel.add_plugin(self._summarize_tool, plugin_name="summarize_tool")
