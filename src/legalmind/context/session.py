"""Session state management backed by Azure Cosmos DB."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from legalmind.services.cosmos_client import CosmosService, get_cosmos_service

logger = structlog.get_logger(__name__)


class SessionManager:
    """Manages user sessions persisted in Cosmos DB.

    Each session tracks:
    - Conversation messages
    - Active document references
    - Current case context
    - User preferences
    - Agent state (which agent was last active, handoff history)

    Enables resumable conversations across browser sessions.
    """

    def __init__(self, *, cosmos_service: CosmosService | None = None) -> None:
        self._cosmos = cosmos_service or get_cosmos_service()

    async def create_session(
        self,
        user_id: str,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new conversation session.

        Args:
            user_id: The user creating the session.
            title: Optional session title.
            metadata: Optional initial metadata.

        Returns:
            The new session ID.
        """
        session_id = str(uuid.uuid4())
        session_metadata = {
            "title": title or "New Conversation",
            "active_documents": [],
            "active_agent": None,
            "conversation_summary": None,
            **(metadata or {}),
        }

        await self._cosmos.create_session(session_id, user_id, metadata=session_metadata)
        logger.info("session_created", session_id=session_id, user_id=user_id)
        return session_id

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The full session document, or None.
        """
        return await self._cosmos.get_session(session_id)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        agent_name: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist a message to the session.

        Args:
            session_id: The session to update.
            role: Message role ('user', 'assistant', 'system').
            content: Message text.
            agent_name: Which agent generated this response.
            citations: Source citations for RAG responses.
            tool_calls: Tool calls made during this turn.
        """
        metadata: dict[str, Any] = {}
        if agent_name:
            metadata["agent_name"] = agent_name
        if citations:
            metadata["citations"] = citations
        if tool_calls:
            metadata["tool_calls"] = tool_calls

        await self._cosmos.append_message(session_id, role, content, metadata=metadata)

    async def get_messages(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Get conversation messages for a session.

        Args:
            session_id: The session identifier.
            limit: Optional limit on number of recent messages.

        Returns:
            List of message dicts.
        """
        return await self._cosmos.get_messages(session_id, limit=limit)

    async def update_session_metadata(self, session_id: str, **kwargs: Any) -> None:
        """Update session metadata fields.

        Args:
            session_id: The session to update.
            **kwargs: Key-value pairs to update in session metadata.
        """
        session = await self._cosmos.get_session(session_id)
        if not session:
            logger.warning("session_not_found_for_update", session_id=session_id)
            return

        session["metadata"].update(kwargs)
        # Re-save via cosmos (using the existing replace pattern)
        container = await self._cosmos._get_container(self._cosmos._sessions_container)
        await container.replace_item(item=session_id, body=session)
        logger.info("session_metadata_updated", session_id=session_id, keys=list(kwargs.keys()))

    async def set_active_document(self, session_id: str, document_id: str, document_name: str) -> None:
        """Set the currently active document in a session.

        Args:
            session_id: The session to update.
            document_id: Document identifier.
            document_name: Human-readable document name.
        """
        session = await self._cosmos.get_session(session_id)
        if not session:
            return

        active_docs = session["metadata"].get("active_documents", [])
        # Avoid duplicates
        if not any(d["id"] == document_id for d in active_docs):
            active_docs.append({"id": document_id, "name": document_name})
            await self.update_session_metadata(session_id, active_documents=active_docs)

    async def set_active_agent(self, session_id: str, agent_name: str) -> None:
        """Track which agent is currently handling the conversation.

        Args:
            session_id: The session to update.
            agent_name: Name of the active agent.
        """
        await self.update_session_metadata(session_id, active_agent=agent_name)

    async def list_user_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """List all sessions for a user.

        Args:
            user_id: The user identifier.

        Returns:
            List of session summary dicts.
        """
        return await self._cosmos.list_sessions(user_id)
