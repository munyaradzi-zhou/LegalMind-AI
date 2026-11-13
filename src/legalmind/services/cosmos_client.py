"""Azure Cosmos DB service client for conversation history and session state."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

import structlog
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from legalmind.config import get_async_azure_credential, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)


class InMemoryContainer:
    """Stand-in for a Cosmos DB container, used when no Cosmos endpoint is configured.

    Supports the container methods LegalMind uses. Data lives only as long as the
    API process, so it is meant for local development without Azure.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self._items[body["id"]] = copy.deepcopy(body)
        return copy.deepcopy(body)

    async def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.create_item(body)

    async def replace_item(self, item: str, body: dict[str, Any]) -> dict[str, Any]:
        if item not in self._items:
            raise CosmosResourceNotFoundError(message=f"Item {item} not found")  # type: ignore[no-untyped-call]
        return await self.create_item(body)

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        if item not in self._items:
            raise CosmosResourceNotFoundError(message=f"Item {item} not found")  # type: ignore[no-untyped-call]
        return copy.deepcopy(self._items[item])

    async def delete_item(self, item: str, partition_key: str) -> None:
        if self._items.pop(item, None) is None:
            raise CosmosResourceNotFoundError(message=f"Item {item} not found")  # type: ignore[no-untyped-call]

    async def query_items(
        self, query: str, parameters: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Only the `WHERE c.user_id = @user_id` filter used by LegalMind is supported."""
        user_id = next((p["value"] for p in parameters or [] if p["name"] == "@user_id"), None)
        for doc in list(self._items.values()):
            if user_id is None or doc.get("user_id") == user_id:
                yield copy.deepcopy(doc)


class CosmosService:
    """Wrapper around Azure Cosmos DB for session state and conversation memory.

    Uses two containers:
    - sessions: Stores conversation history and session metadata.
    - memory: Stores long-term user preferences and frequently accessed cases.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._endpoint = settings.cosmos.endpoint
        self._database_name = settings.cosmos.database
        self._sessions_container = settings.cosmos.container_sessions
        self._memory_container = settings.cosmos.container_memory
        self._client: CosmosClient | None = None
        # Without an endpoint (local development), keep data in memory instead.
        self._in_memory: dict[str, InMemoryContainer] | None = None if self._endpoint else {}
        if self._in_memory is not None:
            logger.warning("cosmos_in_memory", reason="AZURE_COSMOS_ENDPOINT not set; data is lost on restart")

    def _get_client(self) -> CosmosClient:
        """Return the shared client, creating it on first use.

        One client is reused for every call: it keeps a connection pool open,
        so creating a new client per call would leak connections.
        """
        if self._client is None:
            self._client = CosmosClient(url=self._endpoint, credential=self._credential)
        return self._client

    async def _get_container(self, container_name: str) -> Any:
        """Get a reference to a Cosmos DB container."""
        if self._in_memory is not None:
            return self._in_memory.setdefault(container_name, InMemoryContainer())
        client = self._get_client()
        database = client.get_database_client(self._database_name)
        return database.get_container_client(container_name)

    async def close(self) -> None:
        """Close the shared client's connections (called at application shutdown)."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ─── Session Operations ──────────────────────────────────────────────

    async def create_session(
        self, session_id: str, user_id: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a new conversation session.

        Args:
            session_id: Unique session identifier.
            user_id: The user who owns this session.
            metadata: Optional session metadata.

        Returns:
            The created session document.
        """
        container = await self._get_container(self._sessions_container)

        session = {
            "id": session_id,
            "user_id": user_id,
            "messages": [],
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        result: dict[str, Any] = await container.create_item(body=session)
        logger.info("session_created", session_id=session_id, user_id=user_id)
        return result

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The session document, or None if not found.
        """
        container = await self._get_container(self._sessions_container)
        try:
            return cast("dict[str, Any]", await container.read_item(item=session_id, partition_key=session_id))
        except CosmosResourceNotFoundError:
            logger.warning("session_not_found", session_id=session_id)
            return None

    async def append_message(
        self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Append a message to an existing session.

        Args:
            session_id: The session to update.
            role: Message role ('user', 'assistant', 'system').
            content: Message content.
            metadata: Optional message-level metadata (citations, tool calls, etc.).
        """
        container = await self._get_container(self._sessions_container)

        session = await self.get_session(session_id)
        if not session:
            logger.warning("cannot_append_message", session_id=session_id, reason="session not found")
            return

        message = {
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "timestamp": datetime.now(UTC).isoformat(),
        }

        session["messages"].append(message)
        session["updated_at"] = datetime.now(UTC).isoformat()

        await container.replace_item(item=session_id, body=session)
        logger.info("message_appended", session_id=session_id, role=role)

    async def get_messages(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Get messages for a session, optionally limited to the most recent N.

        Args:
            session_id: The session identifier.
            limit: Optional max number of most recent messages.

        Returns:
            List of message dicts.
        """
        session = await self.get_session(session_id)
        if not session:
            return []

        messages: list[dict[str, Any]] = session.get("messages", [])
        if limit:
            return messages[-limit:]
        return messages

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """List all sessions for a user.

        Args:
            user_id: The user identifier.

        Returns:
            List of session summaries.
        """
        container = await self._get_container(self._sessions_container)

        query = "SELECT c.id, c.created_at, c.updated_at, c.metadata FROM c WHERE c.user_id = @user_id"
        parameters = [{"name": "@user_id", "value": user_id}]

        items = []
        async for item in container.query_items(query=query, parameters=parameters):
            items.append(item)

        return items

    # ─── Memory Operations ───────────────────────────────────────────────

    async def store_memory(self, user_id: str, memory_key: str, content: dict[str, Any]) -> dict[str, Any]:
        """Store a long-term memory entry for a user.

        Args:
            user_id: The user identifier.
            memory_key: A unique key for this memory (e.g., 'preference:language').
            content: The memory content to store.

        Returns:
            The stored memory document.
        """
        container = await self._get_container(self._memory_container)

        memory = {
            "id": f"{user_id}:{memory_key}",
            "user_id": user_id,
            "memory_key": memory_key,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        result: dict[str, Any] = await container.upsert_item(body=memory)
        logger.info("memory_stored", user_id=user_id, memory_key=memory_key)
        return result

    async def get_memory(self, user_id: str, memory_key: str) -> dict[str, Any] | None:
        """Retrieve a specific memory entry.

        Args:
            user_id: The user identifier.
            memory_key: The memory key to look up.

        Returns:
            The memory content, or None if not found.
        """
        container = await self._get_container(self._memory_container)
        item_id = f"{user_id}:{memory_key}"
        try:
            return cast("dict[str, Any]", await container.read_item(item=item_id, partition_key=item_id))
        except CosmosResourceNotFoundError:
            return None

    async def get_all_memories(self, user_id: str) -> list[dict[str, Any]]:
        """Retrieve all memory entries for a user.

        Args:
            user_id: The user identifier.

        Returns:
            List of memory documents.
        """
        container = await self._get_container(self._memory_container)

        query = "SELECT * FROM c WHERE c.user_id = @user_id"
        parameters = [{"name": "@user_id", "value": user_id}]

        items = []
        async for item in container.query_items(query=query, parameters=parameters):
            items.append(item)

        return items


@lru_cache(maxsize=1)
def get_cosmos_service() -> CosmosService:
    """Get the application-wide CosmosService (one shared connection pool)."""
    return CosmosService()
