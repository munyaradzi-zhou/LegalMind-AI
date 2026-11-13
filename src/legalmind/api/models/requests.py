"""Pydantic request models for the LegalMind AI API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str = Field(..., description="The user's message", min_length=1, max_length=10000)
    session_id: str | None = Field(None, description="Session ID for continuing a conversation")
    user_id: str = Field(default="anonymous", description="User identifier")
    stream: bool = Field(default=False, description="Whether to stream the response via SSE")
    history: list[ChatMessage] | None = Field(None, description="Optional conversation history override")
    document_ids: list[str] | None = Field(None, description="Limit search to specific documents")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message": "Wat zijn de opzegvoorwaarden in dit contract?",
                    "session_id": "abc-123",
                    "stream": False,
                }
            ],
        },
    }


class SearchRequest(BaseModel):
    """Request body for the direct search endpoint."""

    query: str = Field(..., description="Search query text", min_length=1, max_length=2000)
    top: int = Field(default=10, description="Number of results to return", ge=1, le=50)
    document_type: str | None = Field(
        None,
        description="Filter by document type: contract, correspondence, case_file, policy",
    )
    language: str | None = Field(None, description="Filter by language code (e.g., 'nl', 'en')")
    use_semantic_ranking: bool = Field(default=True, description="Enable semantic re-ranking")
    use_query_rewriting: bool = Field(default=True, description="Enable LLM query rewriting")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "aansprakelijkheid bij arbeidsongeval",
                    "top": 5,
                    "document_type": "case_file",
                }
            ],
        },
    }


class DocumentIngestRequest(BaseModel):
    """Metadata for document ingestion (file uploaded as multipart form data)."""

    document_type: str = Field(
        default="contract",
        description="Type of legal document: contract, correspondence, case_file, policy",
    )
    language: str = Field(default="nl", description="Document language code")
    title: str | None = Field(None, description="Document title (auto-detected if not provided)")
    tags: list[str] = Field(default_factory=list, description="Optional tags for categorization")
