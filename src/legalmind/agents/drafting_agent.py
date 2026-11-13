"""Legal Drafting specialist agent.

Generates professional legal documents, response letters, case summaries,
and client communications in Dutch. Uses the DraftTool (for GPT-4o-powered
text generation) and TranslateTool (for Dutch/English translation).
"""

from __future__ import annotations

from typing import Any

from legalmind.agents.base_agent import BaseAgent
from legalmind.agents.prompts.few_shot_examples import DRAFTING_FEW_SHOT_EXAMPLES
from legalmind.agents.prompts.system_prompts import DRAFTING_AGENT_PROMPT
from legalmind.agents.tools.draft_tool import DraftTool
from legalmind.agents.tools.translate_tool import TranslateTool


class DraftingAgent(BaseAgent):
    """Specialist agent for drafting legal texts in Dutch.

    Capabilities:
    - Draft response letters (reactiebrieven) to opposing parties and courts
    - Write case summaries (zaaksamenvattingen) for internal use
    - Generate client communications in accessible Dutch
    - Create legal memoranda for legal teams
    - Draft demand letters (sommatiebrieven) with proper legal basis
    - Compose settlement proposals (schikkingsvoorstellen)
    - Translate between Dutch and English for international matters

    All output defaults to formal Dutch (juridisch Nederlands) unless
    explicitly instructed otherwise.

    Tools:
    - ``draft_tool``: GPT-4o-powered legal text generation
    - ``translate_tool``: Azure Translator for Dutch/English translation
    """

    def __init__(
        self,
        *,
        draft_tool: DraftTool | None = None,
        translate_tool: TranslateTool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Legal Drafting agent.

        Args:
            draft_tool: Optional pre-configured DraftTool instance.
            translate_tool: Optional pre-configured TranslateTool instance.
            **kwargs: Additional arguments passed to BaseAgent.
        """
        self._draft_tool = draft_tool or DraftTool()
        self._translate_tool = translate_tool or TranslateTool()
        super().__init__(temperature=0.4, **kwargs)

        # Inject few-shot examples into history for better output quality
        for example in DRAFTING_FEW_SHOT_EXAMPLES:
            self.add_context_message(role=example["role"], content=example["content"])

    @property
    def name(self) -> str:
        return "drafting"

    @property
    def description(self) -> str:
        return (
            "Drafts professional legal documents in Dutch — response letters, case "
            "summaries, client communications, demand letters, and more."
        )

    @property
    def system_prompt(self) -> str:
        return DRAFTING_AGENT_PROMPT

    def _register_plugins(self) -> None:
        """Register drafting and translation tools as Semantic Kernel plugins."""
        self._kernel.add_plugin(self._draft_tool, plugin_name="draft_tool")
        self._kernel.add_plugin(self._translate_tool, plugin_name="translate_tool")
