"""Document Analysis specialist agent.

Analyzes, summarizes, compares, and extracts structured information from
Dutch legal documents. Uses the SearchTool (for finding related documents)
and ExtractTool (for NER and key phrase extraction).
"""

from __future__ import annotations

from typing import Any

from legalmind.agents.base_agent import BaseAgent
from legalmind.agents.prompts.few_shot_examples import DOCUMENT_FEW_SHOT_EXAMPLES
from legalmind.agents.prompts.system_prompts import DOCUMENT_AGENT_PROMPT
from legalmind.agents.tools.extract_tool import ExtractTool
from legalmind.agents.tools.search_tool import SearchTool


class DocumentAgent(BaseAgent):
    """Specialist agent for legal document analysis.

    Capabilities:
    - Summarize legal documents (contracts, rulings, correspondence)
    - Extract clauses, parties, dates, and monetary amounts
    - Compare two document versions and highlight differences
    - Identify missing sections or non-standard terms
    - Flag potentially risky or unfavorable clauses

    Tools:
    - ``search_tool``: Hybrid search over the legal knowledge base
    - ``extract_tool``: NER and key phrase extraction via Azure AI Language
    """

    def __init__(
        self,
        *,
        search_tool: SearchTool | None = None,
        extract_tool: ExtractTool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Document Analysis agent.

        Args:
            search_tool: Optional pre-configured SearchTool instance.
            extract_tool: Optional pre-configured ExtractTool instance.
            **kwargs: Additional arguments passed to BaseAgent.
        """
        self._search_tool = search_tool or SearchTool()
        self._extract_tool = extract_tool or ExtractTool()
        super().__init__(**kwargs)

        # Inject few-shot examples into history for better response quality
        for example in DOCUMENT_FEW_SHOT_EXAMPLES:
            self.add_context_message(role=example["role"], content=example["content"])

    @property
    def name(self) -> str:
        return "document_analysis"

    @property
    def description(self) -> str:
        return (
            "Analyzes legal documents — summarization, clause extraction, document "
            "comparison, gap analysis, and risk identification for Dutch legal texts."
        )

    @property
    def system_prompt(self) -> str:
        return DOCUMENT_AGENT_PROMPT

    def _register_plugins(self) -> None:
        """Register search and extraction tools as Semantic Kernel plugins."""
        self._kernel.add_plugin(self._search_tool, plugin_name="search_tool")
        self._kernel.add_plugin(self._extract_tool, plugin_name="extract_tool")
