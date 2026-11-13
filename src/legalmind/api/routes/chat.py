"""Chat endpoint with streaming (SSE) and non-streaming support."""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from legalmind.agents.orchestrator import Orchestrator
from legalmind.api.models.requests import ChatRequest
from legalmind.api.models.responses import ChatResponse, Citation, Grounding
from legalmind.config import get_settings
from legalmind.context.conversation import ConversationManager
from legalmind.context.guardrails import GroundednessResult, GuardrailVerdict, ResponsibleAIGuardrails
from legalmind.context.session import SessionManager
from legalmind.ingestion.document_context import strip_context_header
from legalmind.retrieval.context_builder import ContextBuilder
from legalmind.retrieval.hybrid_search import HybridSearchEngine, SearchFilters
from legalmind.retrieval.query_rewriter import QueryRewriter
from legalmind.services.audit_logger import AuditLogger
from legalmind.services.azure_openai import AzureOpenAIService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse | EventSourceResponse:
    """Process a chat message through the agentic pipeline.

    Flow:
    1. Load or create session
    2. Rewrite query for optimal retrieval
    3. Retrieve relevant legal document chunks
    4. Route to the appropriate specialist agent
    5. Return response with citations

    Supports streaming via Server-Sent Events when `stream=True`.
    """
    logger.info("chat_request", user_id=request.user_id, session_id=request.session_id, stream=request.stream)

    # Initialize services
    session_manager = SessionManager()
    openai_service = AzureOpenAIService()
    search_engine = HybridSearchEngine(openai_service=openai_service)
    query_rewriter = QueryRewriter(openai_service=openai_service)
    context_builder = ContextBuilder()
    orchestrator = Orchestrator(openai_service=openai_service)
    guardrails = ResponsibleAIGuardrails(openai_service=openai_service)
    audit = AuditLogger()

    # Session management
    session_id = request.session_id or str(uuid.uuid4())
    session = await session_manager.get_session(session_id)
    if not session:
        session_id = await session_manager.create_session(request.user_id, title=request.message[:50])

    # Store user message
    await session_manager.add_message(session_id, "user", request.message)

    # ── Pre-flight guardrails: check user input ──────────────────────
    input_check = await guardrails.check_input(request.message)

    await audit.log_safety_check(
        check_type="input",
        is_safe=input_check.content_safety.is_safe if input_check.content_safety else True,
        action=input_check.verdict.value,
        flagged_categories=input_check.content_safety.flagged_categories if input_check.content_safety else [],
        session_id=session_id,
        user_id=request.user_id,
    )

    if input_check.pii_detection and input_check.pii_detection.has_pii:
        await audit.log_pii_detection(
            check_type="input",
            entity_count=input_check.pii_detection.entity_count,
            categories_found=input_check.pii_detection.categories_found,
            session_id=session_id,
            user_id=request.user_id,
        )

    if input_check.verdict == GuardrailVerdict.BLOCK:
        await audit.log_blocked_request(
            direction="input",
            reason=input_check.blocked_reason,
            flagged_categories=input_check.content_safety.flagged_categories if input_check.content_safety else [],
            session_id=session_id,
            user_id=request.user_id,
        )
        return ChatResponse(
            message=input_check.blocked_reason,
            session_id=session_id,
            agent_name="guardrails",
            citations=[],
        )

    # Load conversation history
    conversation = ConversationManager(openai_service=openai_service)
    history = await session_manager.get_messages(session_id, limit=20)
    conversation.load_from_session(history)

    search_results = []
    if get_settings().search.endpoint:
        # LLM query rewriting is optional: on our evaluation it added ~1.3 s without
        # improving retrieval (hybrid search + semantic ranking already handles synonyms).
        query = request.message
        if get_settings().query_rewriting_enabled:
            query = (await query_rewriter.rewrite_query(request.message)).rewritten

        # Retrieve relevant context (optionally limited to specific documents)
        filters = SearchFilters(document_ids=request.document_ids) if request.document_ids else None
        search_results = await search_engine.hybrid_search(query, filters=filters, top=8)
    else:
        # Local development without Azure AI Search: answer without document context.
        logger.warning("search_skipped", reason="AZURE_SEARCH_ENDPOINT not set")

    # Build context with citation markers [1], [2], ...
    context_result = context_builder.build(search_results)
    context = context_result.context

    # Route to the appropriate specialist agent. The retrieved excerpts and recent
    # conversation turns are passed as additional context for the agent. The newest
    # stored message is the current question itself, so it is left out here.
    recent_turns = [
        f"{m['role']}: {m['content'][:500]}"
        for m in conversation.get_context_messages("")[1:-1]
        if m["role"] in ("user", "assistant")
    ][-10:]
    additional_context: dict[str, Any] = {}
    if context:
        additional_context["retrieved_documents"] = context
    if recent_turns:
        additional_context["conversation_history"] = recent_turns

    orchestrator_response = await orchestrator.process_message(
        request.message,
        conversation_id=session_id,
        additional_context=additional_context or None,
    )
    routing = orchestrator_response.routing
    agent_result = orchestrator_response.agent_response

    await audit.log_agent_routing(
        intent=routing.intent.value,
        agent_name=agent_result.agent_name,
        confidence=routing.confidence,
        secondary_intents=[i.value for i in routing.secondary_intents],
        session_id=session_id,
        user_id=request.user_id,
    )

    if not agent_result.success or not agent_result.content:
        logger.error("agent_failed", agent=agent_result.agent_name, error=agent_result.error)
        agent_result.content = "Er is een fout opgetreden bij het verwerken van uw vraag. Probeer het opnieuw."

    agent_response = {"content": agent_result.content, "agent_name": agent_result.agent_name}

    # ── Post-flight guardrails: check AI output ──────────────────────
    output_check = await guardrails.check_output(agent_response["content"], context, question=request.message)

    await audit.log_safety_check(
        check_type="output",
        is_safe=output_check.content_safety.is_safe if output_check.content_safety else True,
        action=output_check.verdict.value,
        flagged_categories=output_check.content_safety.flagged_categories if output_check.content_safety else [],
        session_id=session_id,
        user_id=request.user_id,
    )

    if output_check.groundedness:
        await audit.log_groundedness_check(
            score=output_check.groundedness.score,
            is_grounded=output_check.groundedness.is_grounded,
            ungrounded_claim_count=len(output_check.groundedness.ungrounded_claims),
            session_id=session_id,
            user_id=request.user_id,
        )

    response_content = agent_response["content"]
    grounding, groundedness_score = grounding_status(context, output_check.groundedness)
    if output_check.verdict == GuardrailVerdict.BLOCK:
        response_content = output_check.blocked_reason
        await audit.log_blocked_request(
            direction="output",
            reason=output_check.blocked_reason,
            flagged_categories=output_check.content_safety.flagged_categories if output_check.content_safety else [],
            session_id=session_id,
            user_id=request.user_id,
        )

    # Build citation models for the sources the answer actually uses: a reply that says
    # "the documents don't cover this" shouldn't list 8 unrelated passages.
    # Look up each cited chunk's text and score by chunk id.
    shown = sources_used(response_content, output_check.groundedness)
    results_by_id = {r.id: r for r in search_results}
    citations = []
    for c in context_result.citations:
        if shown is not None and c.number not in shown:
            continue
        source = results_by_id.get(c.chunk_id)
        citations.append(
            Citation(
                index=c.number,
                document_id=c.document_id,
                document_title=c.title,
                chunk_content=strip_context_header(source.content)[:200] if source else "",
                page_number=c.page_number,
                relevance_score=(source.reranker_score or source.search_score) if source else 0.0,
            )
        )

    # Store assistant response
    await session_manager.add_message(
        session_id,
        "assistant",
        response_content,
        agent_name=agent_response.get("agent_name", "orchestrator"),
        citations=[c.model_dump() for c in citations],
    )

    # Summarize if conversation is long
    await conversation.maybe_summarize()

    if request.stream:
        return EventSourceResponse(
            _stream_response(
                response_content,
                session_id,
                agent_response.get("agent_name", "orchestrator"),
                citations,
                grounding,
                groundedness_score,
            ),
        )

    return ChatResponse(
        message=response_content,
        session_id=session_id,
        agent_name=agent_response.get("agent_name", "orchestrator"),
        citations=citations,
        grounding=grounding,
        groundedness_score=groundedness_score,
    )


_CITATION_MARKER = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def cited_numbers(text: str) -> set[int]:
    """Source numbers referenced in an answer: "[1]", "[2, 3]" and "[4][5]" all count."""
    return {int(n) for group in _CITATION_MARKER.findall(text) for n in re.split(r"\s*[,;]\s*", group)}


def sources_used(answer: str, groundedness: GroundednessResult | None) -> set[int] | None:
    """Source numbers to show with an answer; None means show all of them.

    Answers cite as "[1]" but also as "(art. 12.1a)" or "(Bron: polis.txt)", so the numbers
    in the text are combined with the sources the groundedness check found the answer relies on.
    Without a verdict from that check there is no way to tell, so nothing is hidden.
    """
    if groundedness is None or not groundedness.verified:
        return None
    return cited_numbers(answer) | set(groundedness.used_sources)


def grounding_status(context: str, groundedness: GroundednessResult | None) -> tuple[Grounding, float | None]:
    """Tell the user whether the answer is backed by the retrieved documents."""
    if not context.strip():
        return "no_documents", None
    if groundedness is None or not groundedness.verified:
        return "unverified", None
    if not groundedness.is_grounded:
        return "not_grounded", groundedness.score
    return "documents", groundedness.score


async def _stream_response(
    content: str,
    session_id: str,
    agent_name: str,
    citations: list[Citation],
    grounding: Grounding,
    groundedness_score: float | None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream the response as SSE events.

    Yields:
        SSE events with chunks of the response.
    """
    # Stream the content word by word
    words = content.split(" ")
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        yield {
            "event": "message",
            "data": json.dumps({"content": chunk, "done": False}),
        }

    # Final event with metadata
    yield {
        "event": "message",
        "data": json.dumps(
            {
                "content": "",
                "done": True,
                "session_id": session_id,
                "agent_name": agent_name,
                "citations": [c.model_dump() for c in citations],
                "grounding": grounding,
                "groundedness_score": groundedness_score,
            }
        ),
    }
