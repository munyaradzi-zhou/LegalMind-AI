"""Multi-agent orchestrator for LegalMind AI.

Routes user messages to the correct specialist agent based on GPT-4o intent
classification. Manages agent lifecycle, conversation context across handoffs,
and returns structured responses with metadata about which agent handled each request.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from legalmind.agents.base_agent import AgentResponse, BaseAgent
from legalmind.agents.prompts.system_prompts import ORCHESTRATOR_PROMPT
from legalmind.services import AzureOpenAIService

logger = structlog.get_logger(__name__)


class Intent(StrEnum):
    """Supported agent intents for message routing."""

    DOCUMENT_ANALYSIS = "document_analysis"
    LEGAL_RESEARCH = "legal_research"
    DRAFTING = "drafting"
    COMPLIANCE = "compliance"
    GENERAL = "general"


@dataclass
class RoutingDecision:
    """Result of the intent classification step.

    Attributes:
        intent: The classified primary intent.
        confidence: Confidence score (0.0 to 1.0).
        reasoning: Why this intent was chosen.
        secondary_intents: Optional additional intents detected.
        requires_context: Whether prior conversation context is needed.
        language_detected: ISO 639-1 code of the user's message language.
    """

    intent: Intent
    confidence: float
    reasoning: str
    secondary_intents: list[Intent] = field(default_factory=list)
    requires_context: bool = False
    language_detected: str = "nl"


@dataclass
class OrchestratorResponse:
    """Full response from the orchestrator including routing metadata.

    Attributes:
        agent_response: The response from the specialist agent.
        routing: The routing decision that determined which agent was used.
        conversation_id: Optional conversation identifier for session tracking.
        handoff_chain: List of agent names if handoffs occurred.
    """

    agent_response: AgentResponse
    routing: RoutingDecision
    conversation_id: str | None = None
    handoff_chain: list[str] = field(default_factory=list)


class Orchestrator:
    """Multi-agent orchestrator that routes messages to specialist agents.

    The orchestrator:
    1. Classifies user intent using GPT-4o with the ORCHESTRATOR_PROMPT
    2. Routes the message to the appropriate specialist agent
    3. Supports agent handoff (one agent can delegate to another)
    4. Manages conversation context across agent switches
    5. Returns structured responses with full metadata

    Usage::

        from legalmind.agents import Orchestrator

        orchestrator = Orchestrator()
        response = await orchestrator.process_message(
            "Kun je dit contract samenvatten?",
            conversation_id="session_123",
        )
        print(response.agent_response.content)
        print(f"Handled by: {response.routing.intent}")
    """

    def __init__(
        self,
        *,
        agents: dict[Intent, BaseAgent] | None = None,
        openai_service: AzureOpenAIService | None = None,
        classification_temperature: float = 0.1,
        enable_handoffs: bool = True,
        max_handoff_depth: int = 3,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            agents: Pre-configured agent instances keyed by intent. If not
                provided, default agents are lazily initialized on first use.
            openai_service: Azure OpenAI service for intent classification.
                If not provided, a new instance is created.
            classification_temperature: Temperature for intent classification
                (lower = more deterministic routing).
            enable_handoffs: Whether to allow agents to delegate to other agents.
            max_handoff_depth: Maximum number of sequential agent handoffs
                to prevent infinite loops.
        """
        self._openai = openai_service or AzureOpenAIService()
        self._classification_temperature = classification_temperature
        self._enable_handoffs = enable_handoffs
        self._max_handoff_depth = max_handoff_depth

        # Agent registry — lazily initialized if not provided
        self._agents: dict[Intent, BaseAgent] = agents or {}
        self._agents_initialized = bool(agents)

        # Conversation state: maps conversation_id to recent context
        self._conversation_contexts: dict[str, _ConversationContext] = {}

        logger.info(
            "orchestrator_initialized",
            agents_provided=bool(agents),
            handoffs_enabled=enable_handoffs,
        )

    # ─── Lazy agent initialization ───────────────────────────────────────

    def _ensure_agents_initialized(self) -> None:
        """Lazily initialize default agents if none were provided."""
        if self._agents_initialized:
            return

        from legalmind.agents.compliance_agent import ComplianceAgent
        from legalmind.agents.document_agent import DocumentAgent
        from legalmind.agents.drafting_agent import DraftingAgent
        from legalmind.agents.research_agent import ResearchAgent

        # Share tool instances across agents for efficiency
        from legalmind.agents.tools import (
            DraftTool,
            ExtractTool,
            SearchTool,
            SummarizeTool,
            TranslateTool,
        )

        search_tool = SearchTool()
        extract_tool = ExtractTool()
        summarize_tool = SummarizeTool()
        draft_tool = DraftTool()
        translate_tool = TranslateTool()

        self._agents = {
            Intent.DOCUMENT_ANALYSIS: DocumentAgent(
                search_tool=search_tool,
                extract_tool=extract_tool,
            ),
            Intent.LEGAL_RESEARCH: ResearchAgent(
                search_tool=search_tool,
                summarize_tool=summarize_tool,
            ),
            Intent.DRAFTING: DraftingAgent(
                draft_tool=draft_tool,
                translate_tool=translate_tool,
            ),
            Intent.COMPLIANCE: ComplianceAgent(
                search_tool=search_tool,
                extract_tool=extract_tool,
            ),
        }
        self._agents_initialized = True
        logger.info("orchestrator_agents_initialized", agent_count=len(self._agents))

    def register_agent(self, intent: Intent, agent: BaseAgent) -> None:
        """Register or replace an agent for a specific intent.

        Args:
            intent: The intent this agent handles.
            agent: The agent instance.
        """
        self._agents[intent] = agent
        logger.info("orchestrator_agent_registered", intent=intent.value, agent=agent.name)

    def list_agents(self) -> list[dict[str, Any]]:
        """Describe the registered specialist agents.

        Returns:
            One dict per agent with its intent, name, description, tool
            (plugin) names, and system prompt.
        """
        self._ensure_agents_initialized()
        return [
            {
                "intent": intent.value,
                "name": agent.name,
                "description": agent.description,
                "tools": agent.tool_names,
                "system_prompt": agent.system_prompt,
            }
            for intent, agent in self._agents.items()
        ]

    # ─── Intent classification ───────────────────────────────────────────

    async def classify_intent(self, user_message: str) -> RoutingDecision:
        """Classify the user's intent using GPT-4o.

        Sends the user message to GPT-4o with the ORCHESTRATOR_PROMPT to
        determine which specialist agent should handle the request.

        Args:
            user_message: The user's message to classify.

        Returns:
            A RoutingDecision with intent, confidence, and reasoning.
        """
        logger.info("classify_intent_start", message_preview=user_message[:80])

        try:
            response = await self._openai.chat_completion(
                messages=[
                    {"role": "system", "content": ORCHESTRATOR_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=self._classification_temperature,
                max_tokens=512,
            )

            raw_content = response["choices"][0]["message"]["content"]
            classification = self._parse_classification(raw_content)

            logger.info(
                "classify_intent_result",
                intent=classification.intent.value,
                confidence=classification.confidence,
                reasoning=classification.reasoning,
            )

            return classification

        except Exception:
            logger.exception("classify_intent_error")
            # Fall back to general intent on classification failure
            return RoutingDecision(
                intent=Intent.GENERAL,
                confidence=0.0,
                reasoning="Intent classification failed; falling back to general.",
            )

    def _parse_classification(self, raw_content: str) -> RoutingDecision:
        """Parse the GPT-4o classification response into a RoutingDecision.

        Handles both clean JSON and JSON embedded in markdown code blocks.

        Args:
            raw_content: Raw text response from GPT-4o.

        Returns:
            Parsed RoutingDecision.
        """
        # Strip markdown code blocks if present
        content = raw_content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (``` markers)
            lines = [line for line in lines if not line.strip().startswith("```")]
            content = "\n".join(lines)

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "classification_json_parse_failed",
                raw_content=raw_content[:200],
            )
            return RoutingDecision(
                intent=Intent.GENERAL,
                confidence=0.3,
                reasoning="Could not parse classification response.",
            )

        # Parse intent
        intent_str = data.get("intent", "general")
        try:
            intent = Intent(intent_str)
        except ValueError:
            logger.warning("classification_unknown_intent", intent=intent_str)
            intent = Intent.GENERAL

        # Parse secondary intents
        secondary_intents: list[Intent] = []
        for sec in data.get("secondary_intents", []):
            with contextlib.suppress(ValueError):
                secondary_intents.append(Intent(sec))

        return RoutingDecision(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
            secondary_intents=secondary_intents,
            requires_context=bool(data.get("requires_context", False)),
            language_detected=data.get("language_detected", "nl"),
        )

    # ─── Message processing ──────────────────────────────────────────────

    async def process_message(
        self,
        user_message: str,
        *,
        conversation_id: str | None = None,
        additional_context: dict[str, Any] | None = None,
        force_intent: Intent | None = None,
    ) -> OrchestratorResponse:
        """Process a user message through the orchestrator pipeline.

        This is the main entry point:
        1. Classify intent (or use forced intent)
        2. Resolve the target agent
        3. Prepare context (conversation history, additional context)
        4. Invoke the agent
        5. Handle potential handoffs
        6. Return the full response with metadata

        Args:
            user_message: The user's message.
            conversation_id: Optional session identifier for context continuity.
            additional_context: Optional extra context (e.g., uploaded document content).
            force_intent: Skip classification and route directly to this intent.

        Returns:
            OrchestratorResponse with the agent's reply and routing metadata.
        """
        start_time = time.monotonic()

        logger.info(
            "orchestrator_process_start",
            conversation_id=conversation_id,
            message_length=len(user_message),
            force_intent=force_intent.value if force_intent else None,
        )

        # Ensure agents are initialized
        self._ensure_agents_initialized()

        # Step 1: Classify intent
        if force_intent:
            routing = RoutingDecision(
                intent=force_intent,
                confidence=1.0,
                reasoning=f"Intent forced to {force_intent.value}.",
            )
        else:
            routing = await self.classify_intent(user_message)

        # Step 2: Handle general intent (no specialist agent needed)
        if routing.intent == Intent.GENERAL:
            agent_response = await self._handle_general_message(user_message, routing, additional_context)
            return OrchestratorResponse(
                agent_response=agent_response,
                routing=routing,
                conversation_id=conversation_id,
                handoff_chain=["orchestrator"],
            )

        # Step 3: Get the target agent
        agent = self._agents.get(routing.intent)
        if agent is None:
            logger.error("orchestrator_agent_not_found", intent=routing.intent.value)
            agent_response = AgentResponse(
                content=("Er is geen specialist beschikbaar voor dit type verzoek. Kunt u uw vraag anders formuleren?"),
                agent_name="orchestrator",
                success=False,
                error=f"No agent registered for intent: {routing.intent.value}",
            )
            return OrchestratorResponse(
                agent_response=agent_response,
                routing=routing,
                conversation_id=conversation_id,
            )

        # Step 4: Prepare context
        context = self._build_agent_context(
            conversation_id=conversation_id,
            routing=routing,
            additional_context=additional_context,
        )

        # Step 5: Invoke the specialist agent
        handoff_chain = [agent.name]
        agent_response = await agent.invoke(user_message, context=context)

        # Step 6: Handle potential handoffs
        if self._enable_handoffs and self._should_handoff(agent_response):
            agent_response, handoff_chain = await self._execute_handoff(
                original_response=agent_response,
                user_message=user_message,
                context=context,
                handoff_chain=handoff_chain,
            )

        # Step 7: Update conversation context
        if conversation_id:
            self._update_conversation_context(
                conversation_id=conversation_id,
                user_message=user_message,
                agent_response=agent_response,
                routing=routing,
            )

        elapsed = time.monotonic() - start_time
        logger.info(
            "orchestrator_process_complete",
            conversation_id=conversation_id,
            intent=routing.intent.value,
            agent=agent_response.agent_name,
            elapsed=elapsed,
            handoff_chain=handoff_chain,
        )

        return OrchestratorResponse(
            agent_response=agent_response,
            routing=routing,
            conversation_id=conversation_id,
            handoff_chain=handoff_chain,
        )

    # ─── General message handling ────────────────────────────────────────

    async def _handle_general_message(
        self,
        user_message: str,
        routing: RoutingDecision,
        additional_context: dict[str, Any] | None = None,
    ) -> AgentResponse:
        """Handle general/clarification messages directly via GPT-4o.

        Used for greetings, meta-questions, and requests that don't need a
        specialist agent. Retrieved documents and conversation history are
        passed on as well: a question the classifier calls "general" may still
        be answerable from the retrieved excerpts.

        Args:
            user_message: The user's message.
            routing: The routing decision.
            additional_context: Retrieved documents / conversation history.

        Returns:
            An AgentResponse from the orchestrator.
        """
        general_system_prompt = (
            "Je bent LegalMind AI, een juridische AI-assistent voor rechtsbijstand. "
            "Je helpt met juridische vragen over het Nederlands recht. "
            "Je kunt documenten analyseren, juridisch onderzoek doen, teksten opstellen, "
            "en compliance controleren. Beantwoord de gebruiker vriendelijk en verwijs "
            "naar je specialistische mogelijkheden wanneer relevant.\n\n"
            "Antwoord in de taal van de gebruiker (Nederlands of Engels)."
        )
        messages = [{"role": "system", "content": general_system_prompt}]
        if additional_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Gebruik de onderstaande context. Staat het antwoord in de opgehaalde "
                        "documentfragmenten, beantwoord de vraag dan daaruit en verwijs naar de bron "
                        "met [1], [2], enz. Staat het er niet in, zeg dat dan.\n\n"
                        + BaseAgent._format_context(additional_context)
                    ),
                }
            )
        messages.append({"role": "user", "content": user_message})

        try:
            response = await self._openai.chat_completion(
                messages=messages,
                temperature=0.5,
                max_tokens=2048,
            )

            content = response["choices"][0]["message"]["content"]

            return AgentResponse(
                content=content,
                agent_name="orchestrator",
                metadata={
                    "intent": routing.intent.value,
                    "confidence": routing.confidence,
                },
                success=True,
            )

        except Exception as exc:
            logger.exception("general_message_error")
            return AgentResponse(
                content="Er is een fout opgetreden. Probeer het opnieuw.",
                agent_name="orchestrator",
                success=False,
                error=str(exc),
            )

    # ─── Agent handoff ───────────────────────────────────────────────────

    @staticmethod
    def _should_handoff(response: AgentResponse) -> bool:
        """Check if an agent's response indicates it wants to hand off.

        An agent signals handoff by including a specific metadata key.

        Args:
            response: The agent's response to check.

        Returns:
            True if the agent wants to hand off to another agent.
        """
        return "handoff_to" in response.metadata

    async def _execute_handoff(
        self,
        original_response: AgentResponse,
        user_message: str,
        context: dict[str, Any] | None,
        handoff_chain: list[str],
    ) -> tuple[AgentResponse, list[str]]:
        """Execute an agent-to-agent handoff.

        When one agent determines that another agent is better suited for
        the request, it can hand off by setting ``handoff_to`` in its
        response metadata.

        Args:
            original_response: Response from the agent requesting handoff.
            user_message: The original user message.
            context: The conversation context.
            handoff_chain: Current chain of agents involved.

        Returns:
            Tuple of (final AgentResponse, updated handoff chain).
        """
        if len(handoff_chain) >= self._max_handoff_depth:
            logger.warning(
                "handoff_max_depth_reached",
                chain=handoff_chain,
                max_depth=self._max_handoff_depth,
            )
            return original_response, handoff_chain

        target_intent_str = original_response.metadata.get("handoff_to", "")
        try:
            target_intent = Intent(target_intent_str)
        except ValueError:
            logger.warning("handoff_invalid_intent", target=target_intent_str)
            return original_response, handoff_chain

        target_agent = self._agents.get(target_intent)
        if target_agent is None:
            logger.warning("handoff_agent_not_found", intent=target_intent.value)
            return original_response, handoff_chain

        # Build handoff context including the original agent's analysis
        handoff_context = dict(context) if context else {}
        handoff_context["handoff_from"] = original_response.agent_name
        handoff_context["prior_analysis"] = original_response.content
        handoff_context["handoff_reason"] = original_response.metadata.get(
            "handoff_reason", "Agent determined a different specialist is needed."
        )

        logger.info(
            "handoff_executing",
            from_agent=original_response.agent_name,
            to_agent=target_agent.name,
        )

        new_response = await target_agent.invoke(user_message, context=handoff_context)
        handoff_chain.append(target_agent.name)

        # Recursively check if the new agent also wants to hand off
        if self._should_handoff(new_response):
            return await self._execute_handoff(
                original_response=new_response,
                user_message=user_message,
                context=handoff_context,
                handoff_chain=handoff_chain,
            )

        return new_response, handoff_chain

    # ─── Conversation context management ─────────────────────────────────

    def _build_agent_context(
        self,
        conversation_id: str | None,
        routing: RoutingDecision,
        additional_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Build the context dict to pass to the specialist agent.

        Combines conversation history context with any additional context
        (e.g., uploaded document content) and routing metadata.

        Args:
            conversation_id: The conversation session ID.
            routing: The routing decision.
            additional_context: Extra context from the caller.

        Returns:
            Combined context dict, or None if no context is available.
        """
        context: dict[str, Any] = {}

        # Add conversation history context if available
        if conversation_id and conversation_id in self._conversation_contexts:
            conv_ctx = self._conversation_contexts[conversation_id]
            if conv_ctx.last_agent != routing.intent.value:
                # Agent switch — provide prior context
                context["previous_agent"] = conv_ctx.last_agent
                context["conversation_summary"] = conv_ctx.summary
            if conv_ctx.recent_topics:
                context["recent_topics"] = conv_ctx.recent_topics

        # Add routing metadata
        if routing.secondary_intents:
            context["secondary_intents"] = [i.value for i in routing.secondary_intents]
        if routing.language_detected != "nl":
            context["user_language"] = routing.language_detected

        # Merge additional context
        if additional_context:
            context.update(additional_context)

        return context if context else None

    def _update_conversation_context(
        self,
        conversation_id: str,
        user_message: str,
        agent_response: AgentResponse,
        routing: RoutingDecision,
    ) -> None:
        """Update the stored conversation context after processing.

        Args:
            conversation_id: The conversation session ID.
            user_message: The user's message.
            agent_response: The agent's response.
            routing: The routing decision.
        """
        if conversation_id not in self._conversation_contexts:
            self._conversation_contexts[conversation_id] = _ConversationContext()

        ctx = self._conversation_contexts[conversation_id]
        ctx.last_agent = agent_response.agent_name
        ctx.last_intent = routing.intent.value
        ctx.message_count += 1

        # Keep a rolling summary of recent messages
        ctx.recent_messages.append(
            {
                "role": "user",
                "content": user_message[:200],
            }
        )
        ctx.recent_messages.append(
            {
                "role": "assistant",
                "agent": agent_response.agent_name,
                "content": agent_response.content[:200] if agent_response.content else "",
            }
        )

        # Keep only the last N message pairs
        max_recent = 10
        if len(ctx.recent_messages) > max_recent * 2:
            ctx.recent_messages = ctx.recent_messages[-(max_recent * 2) :]

        # Update summary
        ctx.summary = (
            f"Conversation with {ctx.message_count} exchanges. "
            f"Last handled by {agent_response.agent_name} ({routing.intent.value}). "
            f"Topics: {', '.join(ctx.recent_topics[-5:]) if ctx.recent_topics else 'n/a'}."
        )

        # Extract topic from routing reasoning
        if routing.reasoning:
            ctx.recent_topics.append(routing.reasoning[:100])
            if len(ctx.recent_topics) > 10:
                ctx.recent_topics = ctx.recent_topics[-10:]

    def clear_conversation(self, conversation_id: str) -> None:
        """Clear stored context for a conversation.

        Args:
            conversation_id: The conversation to clear.
        """
        self._conversation_contexts.pop(conversation_id, None)
        logger.info("conversation_cleared", conversation_id=conversation_id)

    def clear_all_conversations(self) -> None:
        """Clear all stored conversation contexts."""
        count = len(self._conversation_contexts)
        self._conversation_contexts.clear()
        logger.info("all_conversations_cleared", count=count)


@dataclass
class _ConversationContext:
    """Internal state for tracking a conversation across agent switches.

    Not exported — used only by the Orchestrator.
    """

    last_agent: str = ""
    last_intent: str = ""
    message_count: int = 0
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
    summary: str = ""
