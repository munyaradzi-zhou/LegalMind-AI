"""Abstract base agent for LegalMind AI multi-agent system.

Provides the foundation for all specialist agents, integrating Semantic Kernel
for chat completion with tool-calling, conversation history management, and
structured response handling.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.open_ai import (
    AzureChatCompletion,
    AzureChatPromptExecutionSettings,
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents import AuthorRole, ChatHistory, ChatMessageContent, FunctionCallContent

from legalmind.config import get_azure_credential, get_settings

logger = structlog.get_logger(__name__)


@dataclass
class AgentResponse:
    """Structured response from an agent invocation.

    Attributes:
        content: The text content of the agent's response.
        agent_name: Name of the agent that produced the response.
        tool_calls: List of tools that were invoked during processing.
        metadata: Additional metadata (token usage, timing, etc.).
        success: Whether the invocation completed successfully.
        error: Error message if the invocation failed.
    """

    content: str
    agent_name: str
    tool_calls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


class BaseAgent(abc.ABC):
    """Abstract base class for all LegalMind specialist agents.

    Each specialist agent inherits from this class and provides:
    - A unique name and description
    - A system prompt defining its role and capabilities
    - Semantic Kernel plugins (tools) it can invoke
    - An ``invoke`` method that processes user messages via Azure OpenAI

    The base class handles:
    - Semantic Kernel initialization and Azure OpenAI integration
    - Plugin (tool) registration
    - Conversation history management
    - Structured response formatting with metadata

    Example::

        class MyAgent(BaseAgent):
            @property
            def name(self) -> str:
                return "my_agent"

            @property
            def description(self) -> str:
                return "Does something useful"

            @property
            def system_prompt(self) -> str:
                return "You are a helpful assistant."

            def _register_plugins(self) -> None:
                self._kernel.add_plugin(MyTool(), plugin_name="my_tool")
    """

    def __init__(
        self,
        *,
        max_history_messages: int = 50,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        enable_tool_calling: bool = True,
    ) -> None:
        """Initialize the base agent.

        Args:
            max_history_messages: Maximum conversation history messages to retain.
            temperature: Sampling temperature for chat completions (0.0-2.0).
            max_tokens: Maximum tokens for the chat completion response.
            enable_tool_calling: Whether to enable automatic tool/function calling.
        """
        self._max_history = max_history_messages
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._enable_tool_calling = enable_tool_calling

        # Initialize Semantic Kernel
        self._kernel = Kernel()
        self._configure_ai_service()

        # Register agent-specific plugins (tools)
        self._register_plugins()

        # Conversation history (per-agent instance)
        self._history = ChatHistory(system_message=self.system_prompt)

        logger.info(
            "agent_initialized",
            agent=self.name,
            tools_enabled=enable_tool_calling,
            plugin_count=len(self._kernel.plugins),
        )

    # ─── Abstract properties ──────────────────────────────────────────────

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this agent (e.g., 'document_analysis')."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Human-readable description of the agent's capabilities."""

    @property
    @abc.abstractmethod
    def system_prompt(self) -> str:
        """System prompt that defines this agent's role and behavior."""

    @abc.abstractmethod
    def _register_plugins(self) -> None:
        """Register Semantic Kernel plugins (tools) for this agent.

        Called during ``__init__``. Subclasses should add their tools via
        ``self._kernel.add_plugin(ToolInstance(), plugin_name="tool_name")``.
        """

    # ─── Kernel configuration ────────────────────────────────────────────

    def _configure_ai_service(self) -> None:
        """Configure the chat completion service (OpenAI API or Azure OpenAI)."""
        settings = get_settings()
        if settings.uses_openai_api:
            self._kernel.add_service(
                OpenAIChatCompletion(
                    ai_model_id=settings.openai_chat_model,
                    api_key=settings.openai_api_key.get_secret_value(),
                    service_id="legalmind_chat",
                )
            )
            return

        credential = get_azure_credential()

        from azure.identity import get_bearer_token_provider

        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

        service = AzureChatCompletion(
            deployment_name=settings.openai.chat_deployment,
            endpoint=settings.openai.endpoint,
            ad_token_provider=token_provider,
            api_version=settings.openai.api_version,
            service_id="legalmind_chat",
        )
        self._kernel.add_service(service)

    # ─── Conversation history ────────────────────────────────────────────

    @property
    def tool_names(self) -> list[str]:
        """Names of the Semantic Kernel plugins (tools) registered on this agent."""
        return list(self._kernel.plugins.keys())

    @property
    def history(self) -> ChatHistory:
        """Access the conversation history."""
        return self._history

    def clear_history(self) -> None:
        """Clear conversation history, preserving the system message."""
        self._history = ChatHistory(system_message=self.system_prompt)
        logger.debug("agent_history_cleared", agent=self.name)

    def add_context_message(self, role: str, content: str) -> None:
        """Add a context message to the conversation history.

        Useful for injecting prior context when an agent handoff occurs.

        Args:
            role: Message role ('user', 'assistant', or 'system').
            content: The message content.
        """
        self._history.add_message(ChatMessageContent(role=AuthorRole(role), content=content))

    def _trim_history(self) -> None:
        """Trim conversation history to stay within the configured maximum.

        Preserves the system message (index 0) and the most recent messages.
        """
        messages = self._history.messages
        if len(messages) <= self._max_history + 1:  # +1 for system message
            return

        # Keep system message + last N messages
        keep_count = self._max_history
        trimmed = [messages[0], *list(messages[-keep_count:])]
        self._history = ChatHistory()
        for msg in trimmed:
            self._history.add_message(msg)

        logger.debug(
            "agent_history_trimmed",
            agent=self.name,
            kept=len(trimmed),
            removed=len(messages) - len(trimmed),
        )

    # ─── Invocation ──────────────────────────────────────────────────────

    async def invoke(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> AgentResponse:
        """Process a user message and return a structured response.

        This is the primary entry point for agent interaction. It:
        1. Adds the user message to conversation history
        2. Optionally injects context as a system message
        3. Invokes the Semantic Kernel chat completion (with tool calling)
        4. Tracks which tools were called
        5. Returns a structured AgentResponse

        Args:
            user_message: The user's message or query.
            context: Optional additional context (e.g., document content,
                case details, prior agent output) injected as a system message.

        Returns:
            An AgentResponse with the agent's reply and metadata.
        """
        start_time = time.monotonic()

        logger.info(
            "agent_invoke_start",
            agent=self.name,
            message_length=len(user_message),
            has_context=context is not None,
        )

        try:
            # Inject context if provided
            if context:
                context_text = self._format_context(context)
                self._history.add_message(ChatMessageContent(role=AuthorRole.SYSTEM, content=context_text))

            # Add user message
            self._history.add_user_message(user_message)

            # Trim history if needed
            self._trim_history()

            # Configure execution settings (the class must match the configured service)
            settings_class = (
                OpenAIChatPromptExecutionSettings
                if get_settings().uses_openai_api
                else AzureChatPromptExecutionSettings
            )
            execution_settings = settings_class(
                service_id="legalmind_chat",
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            if self._enable_tool_calling and self._kernel.plugins:
                execution_settings.function_choice_behavior = FunctionChoiceBehavior.Auto(
                    auto_invoke=True,
                    maximum_auto_invoke_attempts=5,
                )

            # Invoke chat completion directly on the chat service. (Rendering the history
            # through a "{{$chat_history}}" prompt template is rejected by current
            # Semantic Kernel versions as unsafe content encoding.)
            chat_service = self._kernel.get_service("legalmind_chat", type=ChatCompletionClientBase)
            result = await chat_service.get_chat_message_content(
                chat_history=self._history,
                settings=execution_settings,
                kernel=self._kernel,
            )

            # Extract response content
            response_content = str(result) if result else ""

            # Add assistant response to history
            self._history.add_assistant_message(response_content)

            # Collect tool call metadata
            tool_calls = self._extract_tool_calls(self._history)

            elapsed = time.monotonic() - start_time

            agent_response = AgentResponse(
                content=response_content,
                agent_name=self.name,
                tool_calls=tool_calls,
                metadata={
                    "elapsed_seconds": round(elapsed, 3),
                    "history_length": len(self._history.messages),
                    "temperature": self._temperature,
                },
                success=True,
            )

            logger.info(
                "agent_invoke_complete",
                agent=self.name,
                elapsed=elapsed,
                response_length=len(response_content),
                tool_calls=tool_calls,
            )

            return agent_response

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            logger.exception("agent_invoke_error", agent=self.name, elapsed=elapsed)

            return AgentResponse(
                content="",
                agent_name=self.name,
                success=False,
                error=str(exc),
                metadata={"elapsed_seconds": round(elapsed, 3)},
            )

    # ─── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _format_context(context: dict[str, Any]) -> str:
        """Format a context dictionary into a text string for injection.

        Args:
            context: Key-value pairs of context information.

        Returns:
            Formatted context string.
        """
        parts = ["## Additional Context\n"]
        for key, value in context.items():
            if isinstance(value, list):
                parts.append(f"**{key}**:")
                for item in value:
                    parts.append(f"  - {item}")
            elif isinstance(value, dict):
                import json

                parts.append(f"**{key}**: {json.dumps(value, ensure_ascii=False, default=str)}")
            else:
                parts.append(f"**{key}**: {value}")
        return "\n".join(parts)

    @staticmethod
    def _extract_tool_calls(history: ChatHistory) -> list[str]:
        """Extract tool/function call names from conversation history.

        Scans recent messages for function call records to report which
        tools were used during the last invocation.

        Args:
            history: The conversation history to scan.

        Returns:
            List of tool/function names that were called.
        """
        # Only FunctionCallContent counts: function *results* also have a name and id.
        return [
            item.name
            for message in history.messages
            for item in message.items
            if isinstance(item, FunctionCallContent) and item.name
        ]

    def __repr__(self) -> str:
        return f"<{type(self).__name__}(name={self.name!r})>"
