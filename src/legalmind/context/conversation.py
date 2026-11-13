"""Conversation history management with sliding window and LLM summarization."""

from __future__ import annotations

from typing import Any

import structlog

from legalmind.context.token_manager import TokenManager
from legalmind.services.azure_openai import AzureOpenAIService

logger = structlog.get_logger(__name__)

SUMMARIZATION_PROMPT = """Summarize the following conversation history concisely, preserving:
- Key legal questions and topics discussed
- Important facts, case details, and document references
- Any decisions made or conclusions reached
- Action items or pending questions

Keep the summary focused and factual. Write in the same language as the conversation.

Conversation to summarize:
{conversation}"""


class ConversationManager:
    """Manages conversation history with automatic summarization.

    When the conversation exceeds the token budget, older messages are
    summarized by the LLM and compressed into a single summary message,
    preserving key context while staying within limits.
    """

    def __init__(
        self,
        *,
        openai_service: AzureOpenAIService | None = None,
        token_manager: TokenManager | None = None,
        max_history_messages: int = 50,
        summarize_threshold: int = 30,
    ) -> None:
        """Initialize the conversation manager.

        Args:
            openai_service: Azure OpenAI service for summarization.
            token_manager: Token manager for budget tracking.
            max_history_messages: Maximum messages to keep before forcing summarization.
            summarize_threshold: Number of messages that triggers summarization.
        """
        self._openai = openai_service or AzureOpenAIService()
        self._token_manager = token_manager or TokenManager()
        self._max_messages = max_history_messages
        self._summarize_threshold = summarize_threshold
        self._messages: list[dict[str, Any]] = []
        self._summary: str | None = None

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Get the current conversation messages."""
        return list(self._messages)

    @property
    def summary(self) -> str | None:
        """Get the current conversation summary (from older messages)."""
        return self._summary

    def add_message(self, role: str, content: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Add a message to the conversation history.

        Args:
            role: Message role ('user', 'assistant', 'system').
            content: Message content.
            metadata: Optional metadata (citations, tool calls, agent name).
        """
        self._messages.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
            }
        )

    def get_context_messages(self, system_prompt: str) -> list[dict[str, str]]:
        """Get messages formatted for the LLM, including any summary of older messages.

        Args:
            system_prompt: The system prompt to prepend.

        Returns:
            List of messages ready for the OpenAI API.
        """
        result: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        # Include summary of older messages if available
        if self._summary:
            result.append(
                {
                    "role": "system",
                    "content": f"Summary of earlier conversation:\n{self._summary}",
                }
            )

        # Add recent messages (role + content only, no metadata)
        for msg in self._messages:
            result.append({"role": msg["role"], "content": msg["content"]})

        # Trim to fit token budget
        result = self._token_manager.fit_messages_to_budget(result)

        return result

    async def maybe_summarize(self) -> bool:
        """Summarize older messages if the conversation is getting long.

        Returns:
            True if summarization was performed.
        """
        if len(self._messages) < self._summarize_threshold:
            return False

        logger.info("conversation_summarizing", message_count=len(self._messages))

        # Split: summarize the first 2/3, keep the last 1/3
        split_point = len(self._messages) * 2 // 3
        messages_to_summarize = self._messages[:split_point]
        messages_to_keep = self._messages[split_point:]

        # Build the conversation text to summarize
        conversation_text = "\n".join(f"{msg['role'].upper()}: {msg['content']}" for msg in messages_to_summarize)

        # Include existing summary context if any
        if self._summary:
            conversation_text = f"Previous summary:\n{self._summary}\n\nNew messages:\n{conversation_text}"

        # Call LLM to summarize
        prompt = SUMMARIZATION_PROMPT.format(conversation=conversation_text)
        response = await self._openai.chat_completion(
            messages=[
                {"role": "system", "content": "You are a concise conversation summarizer."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1024,
        )

        self._summary = response["choices"][0]["message"]["content"]
        self._messages = messages_to_keep

        logger.info(
            "conversation_summarized",
            summarized_count=len(messages_to_summarize),
            kept_count=len(messages_to_keep),
            summary_tokens=self._token_manager.count_tokens(self._summary),
        )
        return True

    def clear(self) -> None:
        """Clear all conversation history and summary."""
        self._messages.clear()
        self._summary = None

    def load_from_session(self, messages: list[dict[str, Any]], summary: str | None = None) -> None:
        """Load conversation state from a persisted session.

        Args:
            messages: Previously stored messages.
            summary: Previously stored summary.
        """
        self._messages = messages
        self._summary = summary
