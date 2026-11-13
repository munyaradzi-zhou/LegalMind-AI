"""LLM client for chat completions and embeddings.

Talks to either the OpenAI API (with ``OPENAI_API_KEY``, default model gpt-4o-mini)
or Azure OpenAI (keyless, via Entra ID), depending on ``LLM_PROVIDER``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog
from openai import AsyncAzureOpenAI, AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from legalmind.config import get_azure_credential, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from azure.core.credentials import TokenCredential
    from openai.types.chat import ChatCompletionMessageParam

logger = structlog.get_logger(__name__)


class AzureOpenAIService:
    """Wrapper for chat completions and embeddings.

    - OpenAI API (``settings.uses_openai_api``): authenticates with ``OPENAI_API_KEY``
      and uses ``OPENAI_CHAT_MODEL`` / ``OPENAI_EMBEDDING_MODEL``.
    - Azure OpenAI: uses the configured deployments and authenticates via
      DefaultAzureCredential (Managed Identity in prod, CLI locally).

    The class keeps its original name so existing callers don't change.
    """

    def __init__(self) -> None:
        settings = get_settings()

        self._client: AsyncOpenAI
        if settings.uses_openai_api:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
            self._chat_deployment = settings.openai_chat_model
            self._embedding_deployment = settings.openai_embedding_model
        else:
            self._client = AsyncAzureOpenAI(
                azure_endpoint=settings.openai.endpoint,
                azure_ad_token_provider=self._get_token_provider(get_azure_credential()),
                api_version=settings.openai.api_version,
            )
            self._chat_deployment = settings.openai.chat_deployment
            self._embedding_deployment = settings.openai.embedding_deployment

    @staticmethod
    def _get_token_provider(credential: TokenCredential) -> Callable[[], str]:
        """Create a token provider callable for Azure AD authentication."""
        from azure.identity import get_bearer_token_provider

        return get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a chat completion using GPT-4o.

        Args:
            messages: Conversation messages in OpenAI format.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens to generate.
            tools: Optional tool definitions for function calling.
            tool_choice: Optional tool choice strategy.

        Returns:
            The full response from the OpenAI API as a dict.
        """
        logger.info("chat_completion_request", deployment=self._chat_deployment, message_count=len(messages))

        kwargs: dict[str, Any] = {
            "model": self._chat_deployment,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format  # e.g. {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        logger.info(
            "chat_completion_response",
            usage_prompt=response.usage.prompt_tokens if response.usage else 0,
            usage_completion=response.usage.completion_tokens if response.usage else 0,
        )
        return cast("dict[str, Any]", response.model_dump())

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a chat completion using GPT-4o via Server-Sent Events.

        Yields:
            Chunks of the streamed response.
        """
        stream = await self._client.chat.completions.create(
            model=self._chat_deployment,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate vector embeddings using text-embedding-3-large.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each 3072 dimensions).
        """
        logger.info("embedding_request", text_count=len(texts))

        response = await self._client.embeddings.create(
            model=self._embedding_deployment,
            input=texts,
        )

        embeddings = [item.embedding for item in response.data]
        logger.info("embedding_response", embedding_count=len(embeddings))
        return embeddings

    async def generate_single_embedding(self, text: str) -> list[float]:
        """Generate a single embedding vector.

        Args:
            text: Text string to embed.

        Returns:
            A single embedding vector.
        """
        embeddings = await self.generate_embeddings([text])
        return embeddings[0]
