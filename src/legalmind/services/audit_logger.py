"""Structured audit logging for AI decisions and safety checks.

Records all content safety analyses, PII detections, groundedness checks,
and agent routing decisions to Azure Cosmos DB for compliance and traceability.
Critical for a legal services platform handling sensitive personal data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import structlog

from legalmind.services.cosmos_client import CosmosService, get_cosmos_service

logger = structlog.get_logger(__name__)


class AuditEventType(StrEnum):
    """Types of auditable events."""

    CONTENT_SAFETY_INPUT = "content_safety.input"
    CONTENT_SAFETY_OUTPUT = "content_safety.output"
    PII_DETECTED_INPUT = "pii.detected.input"
    PII_DETECTED_OUTPUT = "pii.detected.output"
    PII_REDACTED = "pii.redacted"
    GROUNDEDNESS_CHECK = "groundedness.check"
    AGENT_ROUTING = "agent.routing"
    INPUT_BLOCKED = "guardrail.input_blocked"
    OUTPUT_BLOCKED = "guardrail.output_blocked"


# Cosmos DB container name for audit logs
AUDIT_CONTAINER = "audit_log"


class AuditLogger:
    """Structured audit logger backed by Azure Cosmos DB.

    Every AI-related decision is recorded with:
    - Unique event ID and timestamp
    - Session and user context
    - Event type and details
    - The safety verdict or routing decision

    This provides a complete audit trail for regulatory compliance
    (AVG/GDPR) and incident investigation.
    """

    def __init__(self, *, cosmos_service: CosmosService | None = None) -> None:
        self._cosmos = cosmos_service or get_cosmos_service()

    async def _log_event(
        self,
        event_type: AuditEventType,
        *,
        session_id: str = "",
        user_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> str:
        """Write an audit event to Cosmos DB.

        Args:
            event_type: The type of auditable event.
            session_id: The conversation session ID.
            user_id: The user who triggered this event.
            details: Event-specific payload.

        Returns:
            The unique event ID.
        """
        event_id = str(uuid4())
        event = {
            "id": event_id,
            "event_type": event_type.value,
            "session_id": session_id,
            "user_id": user_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "details": details or {},
        }

        try:
            container = await self._cosmos._get_container(AUDIT_CONTAINER)
            await container.create_item(body=event)
            logger.info(
                "audit_event_logged",
                event_id=event_id,
                event_type=event_type.value,
                session_id=session_id,
            )
        except Exception:
            # Audit logging should never block the main flow
            logger.exception(
                "audit_log_write_error",
                event_type=event_type.value,
                session_id=session_id,
            )

        return event_id

    # ─── Content Safety Events ───────────────────────────────────────────

    async def log_safety_check(
        self,
        *,
        check_type: str,
        is_safe: bool,
        action: str,
        flagged_categories: list[str],
        session_id: str = "",
        user_id: str = "",
    ) -> str:
        """Log a content safety analysis result.

        Args:
            check_type: "input" or "output".
            is_safe: Whether the content passed safety checks.
            action: The action taken (allow, warn, block).
            flagged_categories: Categories that were flagged.
            session_id: Session context.
            user_id: User context.

        Returns:
            The audit event ID.
        """
        event_type = (
            AuditEventType.CONTENT_SAFETY_INPUT if check_type == "input" else AuditEventType.CONTENT_SAFETY_OUTPUT
        )

        return await self._log_event(
            event_type,
            session_id=session_id,
            user_id=user_id,
            details={
                "is_safe": is_safe,
                "action": action,
                "flagged_categories": flagged_categories,
            },
        )

    # ─── PII Events ──────────────────────────────────────────────────────

    async def log_pii_detection(
        self,
        *,
        check_type: str,
        entity_count: int,
        categories_found: list[str],
        session_id: str = "",
        user_id: str = "",
    ) -> str:
        """Log a PII detection event.

        Note: We log the *categories* and *count* of PII found, but never
        the actual PII values — that would defeat the purpose of PII protection.

        Args:
            check_type: "input" or "output".
            entity_count: Number of PII entities found.
            categories_found: Categories of PII detected.
            session_id: Session context.
            user_id: User context.

        Returns:
            The audit event ID.
        """
        event_type = AuditEventType.PII_DETECTED_INPUT if check_type == "input" else AuditEventType.PII_DETECTED_OUTPUT

        return await self._log_event(
            event_type,
            session_id=session_id,
            user_id=user_id,
            details={
                "entity_count": entity_count,
                "categories_found": categories_found,
            },
        )

    # ─── Groundedness Events ─────────────────────────────────────────────

    async def log_groundedness_check(
        self,
        *,
        score: float,
        is_grounded: bool,
        ungrounded_claim_count: int,
        session_id: str = "",
        user_id: str = "",
    ) -> str:
        """Log a groundedness verification result.

        Args:
            score: Groundedness score (0.0-1.0).
            is_grounded: Whether the response met the threshold.
            ungrounded_claim_count: Number of ungrounded claims.
            session_id: Session context.
            user_id: User context.

        Returns:
            The audit event ID.
        """
        return await self._log_event(
            AuditEventType.GROUNDEDNESS_CHECK,
            session_id=session_id,
            user_id=user_id,
            details={
                "score": score,
                "is_grounded": is_grounded,
                "ungrounded_claim_count": ungrounded_claim_count,
            },
        )

    # ─── Agent Routing Events ────────────────────────────────────────────

    async def log_agent_routing(
        self,
        *,
        intent: str,
        agent_name: str,
        confidence: float = 0.0,
        secondary_intents: list[str] | None = None,
        session_id: str = "",
        user_id: str = "",
    ) -> str:
        """Log an agent routing decision.

        Args:
            intent: The classified user intent.
            agent_name: The agent selected to handle the request.
            confidence: Routing confidence score.
            secondary_intents: Alternative intents considered.
            session_id: Session context.
            user_id: User context.

        Returns:
            The audit event ID.
        """
        return await self._log_event(
            AuditEventType.AGENT_ROUTING,
            session_id=session_id,
            user_id=user_id,
            details={
                "intent": intent,
                "agent_name": agent_name,
                "confidence": confidence,
                "secondary_intents": secondary_intents or [],
            },
        )

    # ─── Blocked Request Events ──────────────────────────────────────────

    async def log_blocked_request(
        self,
        *,
        direction: str,
        reason: str,
        flagged_categories: list[str],
        session_id: str = "",
        user_id: str = "",
    ) -> str:
        """Log a blocked request or response.

        Args:
            direction: "input" or "output".
            reason: Human-readable reason for blocking.
            flagged_categories: Safety categories that triggered the block.
            session_id: Session context.
            user_id: User context.

        Returns:
            The audit event ID.
        """
        event_type = AuditEventType.INPUT_BLOCKED if direction == "input" else AuditEventType.OUTPUT_BLOCKED

        return await self._log_event(
            event_type,
            session_id=session_id,
            user_id=user_id,
            details={
                "reason": reason,
                "flagged_categories": flagged_categories,
            },
        )
