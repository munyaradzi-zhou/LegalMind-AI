"""Semantic Kernel plugin tools for LegalMind AI agents.

Each tool wraps an Azure service (AI Search, Language, Translator, OpenAI) as a
Semantic Kernel kernel function that agents can invoke during chat completion.
"""

from legalmind.agents.tools.draft_tool import DraftTool
from legalmind.agents.tools.extract_tool import ExtractTool
from legalmind.agents.tools.search_tool import SearchTool
from legalmind.agents.tools.summarize_tool import SummarizeTool
from legalmind.agents.tools.translate_tool import TranslateTool

__all__ = [
    "DraftTool",
    "ExtractTool",
    "SearchTool",
    "SummarizeTool",
    "TranslateTool",
]
