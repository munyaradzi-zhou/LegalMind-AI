"""Long-term user memory management backed by Azure Cosmos DB."""

from __future__ import annotations

from typing import Any

import structlog

from legalmind.services.cosmos_client import CosmosService, get_cosmos_service

logger = structlog.get_logger(__name__)


class MemoryManager:
    """Manages long-term user memory across sessions.

    Stores and retrieves:
    - User preferences (language, response style, detail level)
    - Frequently referenced cases or documents
    - Domain-specific context (user's area of law, common case types)
    - Learned facts about the user's workflow

    Memory persists across conversation sessions in Cosmos DB.
    """

    def __init__(self, *, cosmos_service: CosmosService | None = None) -> None:
        self._cosmos = cosmos_service or get_cosmos_service()

    async def remember(self, user_id: str, key: str, value: Any, *, category: str = "general") -> None:
        """Store a memory entry for a user.

        Args:
            user_id: The user identifier.
            key: Memory key (e.g., 'preferred_language', 'default_case_type').
            value: The value to remember.
            category: Memory category for organization.
        """
        content = {
            "value": value,
            "category": category,
        }
        await self._cosmos.store_memory(user_id, key, content)
        logger.info("memory_stored", user_id=user_id, key=key, category=category)

    async def recall(self, user_id: str, key: str) -> Any | None:
        """Recall a specific memory entry.

        Args:
            user_id: The user identifier.
            key: The memory key to look up.

        Returns:
            The stored value, or None if not found.
        """
        memory = await self._cosmos.get_memory(user_id, key)
        if memory:
            return memory["content"]["value"]
        return None

    async def recall_all(self, user_id: str, *, category: str | None = None) -> dict[str, Any]:
        """Recall all memories for a user, optionally filtered by category.

        Args:
            user_id: The user identifier.
            category: Optional category filter.

        Returns:
            Dict mapping memory keys to their values.
        """
        memories = await self._cosmos.get_all_memories(user_id)

        result = {}
        for memory in memories:
            mem_category = memory["content"].get("category", "general")
            if category and mem_category != category:
                continue
            result[memory["memory_key"]] = memory["content"]["value"]

        return result

    async def forget(self, user_id: str, key: str) -> None:
        """Remove a specific memory entry.

        Args:
            user_id: The user identifier.
            key: The memory key to remove.
        """
        container = await self._cosmos._get_container(self._cosmos._memory_container)
        item_id = f"{user_id}:{key}"
        try:
            await container.delete_item(item=item_id, partition_key=item_id)
            logger.info("memory_forgotten", user_id=user_id, key=key)
        except Exception:
            logger.warning("memory_not_found_for_deletion", user_id=user_id, key=key)

    async def build_memory_context(self, user_id: str) -> str:
        """Build a context string from user memories for injection into prompts.

        Args:
            user_id: The user identifier.

        Returns:
            A formatted string of user preferences and context, or empty string.
        """
        memories = await self.recall_all(user_id)
        if not memories:
            return ""

        lines = ["User preferences and context:"]
        for key, value in memories.items():
            lines.append(f"- {key}: {value}")

        return "\n".join(lines)
