"""Token counting and context window budget management using tiktoken."""

from __future__ import annotations

import structlog
import tiktoken

logger = structlog.get_logger(__name__)

# GPT-4o uses cl100k_base encoding
DEFAULT_ENCODING = "cl100k_base"
DEFAULT_MAX_CONTEXT_TOKENS = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 4_096


class TokenManager:
    """Manages token counting and context window budgeting.

    Allocates the context window across:
    - System prompt
    - Conversation history
    - Retrieved context (RAG)
    - Reserved for generation output

    Implements graceful degradation when approaching limits by
    truncating history or retrieved context.
    """

    def __init__(
        self,
        *,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        system_prompt_budget: float = 0.10,
        history_budget: float = 0.25,
        retrieval_budget: float = 0.55,
    ) -> None:
        """Initialize the token manager.

        Args:
            max_context_tokens: Total context window size.
            max_output_tokens: Tokens reserved for generation.
            system_prompt_budget: Fraction of remaining tokens for system prompt.
            history_budget: Fraction of remaining tokens for conversation history.
            retrieval_budget: Fraction of remaining tokens for retrieved context.
        """
        self._encoding = tiktoken.get_encoding(DEFAULT_ENCODING)
        self._max_context = max_context_tokens
        self._max_output = max_output_tokens
        self._available = max_context_tokens - max_output_tokens

        self._system_budget = int(self._available * system_prompt_budget)
        self._history_budget = int(self._available * history_budget)
        self._retrieval_budget = int(self._available * retrieval_budget)

    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in a text string.

        Args:
            text: The text to count tokens for.

        Returns:
            Token count.
        """
        return len(self._encoding.encode(text))

    def count_messages_tokens(self, messages: list[dict[str, str]]) -> int:
        """Count tokens in a list of chat messages.

        Accounts for the per-message overhead in the OpenAI message format.

        Args:
            messages: List of message dicts with 'role' and 'content'.

        Returns:
            Total token count including message formatting overhead.
        """
        tokens = 0
        for message in messages:
            tokens += 4  # message formatting overhead
            tokens += self.count_tokens(message.get("role", ""))
            tokens += self.count_tokens(message.get("content", ""))
        tokens += 2  # reply priming
        return tokens

    def truncate_to_budget(self, text: str, budget: int) -> str:
        """Truncate text to fit within a token budget.

        Args:
            text: The text to potentially truncate.
            budget: Maximum tokens allowed.

        Returns:
            The text, truncated if necessary, with an indicator if truncated.
        """
        tokens = self._encoding.encode(text)
        if len(tokens) <= budget:
            return text

        truncated_tokens = tokens[: budget - 10]  # leave room for truncation notice
        truncated_text = self._encoding.decode(truncated_tokens)
        logger.warning("text_truncated", original_tokens=len(tokens), budget=budget)
        return truncated_text + "\n\n[... truncated to fit context window]"

    def fit_messages_to_budget(
        self,
        messages: list[dict[str, str]],
        budget: int | None = None,
    ) -> list[dict[str, str]]:
        """Trim conversation history to fit within the history budget.

        Keeps the system message (first) and most recent messages.
        Drops older messages from the middle when over budget.

        Args:
            messages: Full conversation history.
            budget: Optional override for the token budget.

        Returns:
            Trimmed message list fitting within budget.
        """
        max_tokens = budget or self._history_budget

        total = self.count_messages_tokens(messages)
        if total <= max_tokens:
            return messages

        # Always keep system message (index 0) and most recent user message (last)
        if len(messages) <= 2:
            return messages

        system_msg = messages[0] if messages[0]["role"] == "system" else None
        remaining = messages[1:] if system_msg else messages

        # Drop oldest messages until we fit
        while self.count_messages_tokens(([system_msg] if system_msg else []) + remaining) > max_tokens:
            if len(remaining) <= 1:
                break
            remaining = remaining[1:]

        result = ([system_msg] if system_msg else []) + remaining
        logger.info(
            "messages_trimmed",
            original_count=len(messages),
            trimmed_count=len(result),
        )
        return result

    def fit_context_to_budget(self, context: str, budget: int | None = None) -> str:
        """Fit retrieved context to the retrieval budget.

        Args:
            context: The assembled retrieval context string.
            budget: Optional override for the token budget.

        Returns:
            Context string fitting within budget.
        """
        max_tokens = budget or self._retrieval_budget
        return self.truncate_to_budget(context, max_tokens)

    def get_budget_report(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        context: str,
    ) -> dict[str, int]:
        """Generate a report of token usage across all budget categories.

        Args:
            system_prompt: The system prompt text.
            messages: Conversation history messages.
            context: Retrieved context.

        Returns:
            Dict with token counts and remaining capacity.
        """
        system_tokens = self.count_tokens(system_prompt)
        history_tokens = self.count_messages_tokens(messages)
        context_tokens = self.count_tokens(context)
        total_used = system_tokens + history_tokens + context_tokens

        return {
            "system_prompt_tokens": system_tokens,
            "system_prompt_budget": self._system_budget,
            "history_tokens": history_tokens,
            "history_budget": self._history_budget,
            "context_tokens": context_tokens,
            "context_budget": self._retrieval_budget,
            "total_used": total_used,
            "total_available": self._available,
            "remaining": self._available - total_used,
            "output_reserved": self._max_output,
        }
